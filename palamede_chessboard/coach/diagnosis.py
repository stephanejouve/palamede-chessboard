"""The coach's diagnosis of a finished game, and one round over the games (#95).

A game is diagnosed once it is over and at least one side is a ``human``
account of the registry. Its mistakes are those of
:func:`~palamede_chessboard.chessboard_engine.get_my_mistakes` as #197
corrected them (a move that mates is no mistake, a forced mate missed
is said in moves), from ``mistake`` up, each with its motif.

The engine never looks at a game in progress : a game whose result is
``*`` is never analysed, and a finished game that shares a position
with a live one (:func:`~palamede_chessboard.chess_engine_guard.mask_walk_entries`)
waits for a later round rather than being reported with holes.

Like every Stockfish path of the server, each analysis goes through
:func:`~palamede_chessboard.chess_engine_guard.check_engine_call` and
leaves a line in the engine log, with ``caller="coach"`` : the coach
runs alone in production, and the fair-play log must see it too.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from palamede_chessboard.chess_engine_guard import (
    check_engine_call,
    is_exercise_game,
    log_engine_call,
    mask_walk_entries,
)
from palamede_chessboard.chessboard_engine import (
    DEFAULT_DEPTH,
    GameWalkEntry,
    analyze_game,
    get_my_mistakes,
)
from palamede_chessboard.chessboard_state import ChessboardError, GameState
from palamede_chessboard.coach import CALLER
from palamede_chessboard.coach.exercises import exercises_of
from palamede_chessboard.coach.motifs import HEURISTIC_MOTIFS, motif_of
from palamede_chessboard.coach.store import add_exercises, game_key, read_report, write_report
from palamede_chessboard.paths import resolve_games_dir
from palamede_chessboard.players import registry

log = logging.getLogger(__name__)

#: The least a move must have cost to be reported.
MIN_CLASSIFICATION = "mistake"
#: How the coach's calls appear in the engine log.
ENGINE_TOOL = "chess_get_my_mistakes"


def human_sides(state: GameState, players: dict) -> dict[str, str]:
    """``{side: player_id}`` of the sides a ``human`` account plays."""
    sides: dict[str, str] = {}
    for side, name in (("white", state.metadata.white), ("black", state.metadata.black)):
        try:
            player_id = registry.player_id_of(name)
        except registry.RegistryError:
            continue  # a name no account carries (an import, a grandmaster)
        if players.get(player_id, {}).get("kind") == "human":
            sides[side] = player_id
    return sides


def _mistake(entry: GameWalkEntry) -> dict:
    motif = motif_of(entry)
    return {
        "ply": entry.ply,
        "san": entry.san,
        "fen_before": entry.fen_before,
        "classification": entry.classification,
        "cp_loss": entry.cp_loss,
        "missed_mate": entry.missed_mate,
        "motif": motif,
        "heuristic": motif in HEURISTIC_MOTIFS,
    }


def diagnose(
    state: GameState,
    sides: dict[str, str],
    games_dir: Path,
    *,
    depth: int = DEFAULT_DEPTH,
    engine_path: str | None = None,
) -> dict | None:
    """The report of a finished game, or ``None`` when it must wait a round.

    Raises :class:`ValueError` on a game in progress : the engine never
    looks at one.
    """
    if state.metadata.result == "*":
        raise ValueError(f"{state.metadata.game_id} is in progress")
    game_id = state.metadata.game_id
    decision = check_engine_call(games_dir, tool=ENGINE_TOOL, game_id=game_id)
    if not decision["allowed"]:
        log_engine_call(tool=ENGINE_TOOL, decision=decision, game_id=game_id, caller=CALLER)
        return None  # refused by the guard : later
    walk = analyze_game(state, depth=depth, engine_path=engine_path, reuse_after=True)
    _walk, masked, details = mask_walk_entries(walk, games_dir)
    log_engine_call(
        tool=ENGINE_TOOL,
        decision=decision,
        game_id=game_id,
        caller=CALLER,
        extra={"masked_plies": details} if details else None,
    )
    if masked:
        return None  # a live game reached one of its positions : later
    report_sides = {}
    for side, player_id in sides.items():
        mistakes = [
            _mistake(e)
            for e in get_my_mistakes(walk, mover=side, min_classification=MIN_CLASSIFICATION)
        ]
        report_sides[side] = {
            "player": player_id,
            "mistakes": mistakes,
            "by_motif": dict(Counter(m["motif"] for m in mistakes)),
        }
    return {
        "game_id": game_id,
        "key": game_key(state),
        "result": state.metadata.result,
        "depth": depth,
        "analyzed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sides": report_sides,
    }


def _diagnose_one(
    state: GameState,
    sides: dict[str, str],
    games_dir: Path,
    *,
    depth: int,
    engine_path: str | None,
) -> int | None:
    """Report and exercises of one game ; how many exercises, ``None`` to wait.

    The report is written last : until it is, the game is diagnosed again
    on the next round, and the exercises already added are not added twice.
    """
    report = diagnose(state, sides, games_dir, depth=depth, engine_path=engine_path)
    if report is None:
        return None
    found = exercises_of(report, games_dir, depth=depth, engine_path=engine_path)
    if found is None:
        return None
    added = sum(add_exercises(games_dir, pid, exos) for pid, exos in found.items() if exos)
    write_report(games_dir, state.metadata.game_id, report)
    return added


def run_once(
    games_dir: Path, *, depth: int = DEFAULT_DEPTH, engine_path: str | None = None
) -> dict:
    """Diagnose every finished game of a human that has no report for its key,
    and draw its exercises.

    Idempotent : a game already reported under the same key is skipped.
    An unreadable registry stops the round (fail closed) ; a game that
    cannot be read or analysed is logged and left for the next round.
    """
    players = registry.load(games_dir)["players"]
    summary: dict = {"diagnosed": [], "waiting": [], "failed": [], "exercises": 0}
    for pgn in sorted(games_dir.glob("*.pgn")):
        game_id = pgn.stem
        if is_exercise_game(game_id):
            continue  # one move from a position : no game to learn from
        try:
            state = GameState.load(game_id, games_dir=games_dir)
        except ChessboardError as exc:
            log.warning("coach: skip %s (%s)", game_id, exc)
            continue
        if state.metadata.result == "*":
            continue
        sides = human_sides(state, players)
        if not sides:
            continue
        existing = read_report(games_dir, game_id)
        if existing is not None and existing.get("key") == game_key(state):
            continue
        try:
            added = _diagnose_one(state, sides, games_dir, depth=depth, engine_path=engine_path)
        except Exception as exc:  # noqa: BLE001 — one game never stops the round
            log.warning("coach: %s not diagnosed (%s)", game_id, exc)
            summary["failed"].append(game_id)
            continue
        if added is None:
            summary["waiting"].append(game_id)
            continue
        summary["diagnosed"].append(game_id)
        summary["exercises"] += added
    return summary


def main(argv: list[str] | None = None) -> int:
    """``palamede-coach once`` : one round over the games directory."""
    parser = argparse.ArgumentParser(prog="palamede-coach", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    once = sub.add_parser("once", help="diagnose the finished games and draw exercises once")
    once.add_argument("--games-dir", type=Path, default=None)
    once.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    summary = run_once(args.games_dir or resolve_games_dir(), depth=args.depth)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
