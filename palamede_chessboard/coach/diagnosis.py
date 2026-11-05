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
runs alone in production, and the fair-play log must see it too. A game
one of whose positions a live game reached waits **before** the engine
is started (:func:`~palamede_chessboard.chess_engine_guard.game_in_play`),
and is checked again after it, in case a live game got there meanwhile.

``palamede-coach run`` makes a round every ``--interval`` seconds until
SIGTERM, remembering between rounds what :mod:`~.rounds` describes.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from palamede_chessboard.chess_engine_guard import (
    check_engine_call,
    game_in_play,
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
from palamede_chessboard.coach.progress import complete_report, game_date, plies_of
from palamede_chessboard.coach.rounds import DEFAULT_INTERVAL, RoundMemory, Wait, mtime_ns
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
) -> dict | Wait:
    """The report of a finished game, or the :class:`Wait` for the live
    games that reached its positions.

    Raises :class:`ValueError` on a game in progress : the engine never
    looks at one.
    """
    if state.metadata.result == "*":
        raise ValueError(f"{state.metadata.game_id} is in progress")
    game_id = state.metadata.game_id
    decision = check_engine_call(games_dir, tool=ENGINE_TOOL, game_id=game_id)
    if decision["allowed"] and (match := game_in_play(state, games_dir)) is not None:
        reason = "a position of the game is in a game in progress"
        decision = {"allowed": False, "matched_game_id": match, "reason": reason}
    if not decision["allowed"]:
        log_engine_call(tool=ENGINE_TOOL, decision=decision, game_id=game_id, caller=CALLER)
        return Wait.on(decision.get("matched_game_id"))  # refused by the guard : later
    walk = analyze_game(state, depth=depth, engine_path=engine_path, reuse_after=True)
    _walk, masked, details = mask_walk_entries(walk, games_dir)
    log_engine_call(
        tool=ENGINE_TOOL,
        decision=decision,
        game_id=game_id,
        caller=CALLER,
        extra={"masked_plies": details} if details else None,
    )
    if masked:  # a live game reached one of its positions : later
        return Wait.on(*(detail["matched_game_id"] for detail in details))
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
            "plies": plies_of(state, side),
        }
    now = datetime.now(UTC)
    date, date_source = game_date(state, now)
    return {
        "game_id": game_id,
        "key": game_key(state),
        "result": state.metadata.result,
        "depth": depth,
        "analyzed_at": now.isoformat(timespec="seconds"),
        # The time axis of the progress, not ``analyzed_at`` (#95 step 2).
        "game_date": date,
        "game_date_source": date_source,
        "sides": report_sides,
    }


def _diagnose_one(
    state: GameState,
    sides: dict[str, str],
    games_dir: Path,
    *,
    depth: int,
    engine_path: str | None,
) -> int | Wait:
    """Report and exercises of one game ; how many exercises, or its :class:`Wait`.

    The report is written last : until it is, the game is diagnosed again
    on the next round, and the exercises already added are not added twice.
    """
    report = diagnose(state, sides, games_dir, depth=depth, engine_path=engine_path)
    if isinstance(report, Wait):
        return report
    found = exercises_of(report, games_dir, depth=depth, engine_path=engine_path)
    if isinstance(found, Wait):
        return found
    added = sum(add_exercises(games_dir, pid, exos) for pid, exos in found.items() if exos)
    write_report(games_dir, state.metadata.game_id, report)
    return added


def _one_game(
    pgn: Path, games_dir: Path, players: dict, *, depth: int, engine_path: str | None
) -> tuple[str, int | Wait]:
    """``(outcome, exercises added)`` ; outcome ``settled``, ``diagnosed``,
    ``waiting`` (with its :class:`Wait`) or ``failed``. Settled : nothing to do
    until the PGN changes."""
    game_id = pgn.stem
    try:
        state = GameState.load(game_id, games_dir=games_dir)
    except ChessboardError as exc:
        log.warning("coach: skip %s (%s)", game_id, exc)
        return "settled", 0
    if state.metadata.result == "*":
        return "settled", 0
    sides = human_sides(state, players)
    if not sides:
        return "settled", 0
    existing = read_report(games_dir, game_id)
    if existing is not None and existing.get("key") == game_key(state):
        if complete_report(existing, state):  # written before game_date and plies (#95)
            write_report(games_dir, game_id, existing)
        return "settled", 0
    try:
        added = _diagnose_one(state, sides, games_dir, depth=depth, engine_path=engine_path)
    except Exception as exc:  # noqa: BLE001 — one game never stops the round
        log.warning("coach: %s not diagnosed (%s)", game_id, exc)
        return "failed", 0
    if isinstance(added, Wait):
        return "waiting", added
    return "diagnosed", added


def run_once(
    games_dir: Path,
    *,
    depth: int = DEFAULT_DEPTH,
    engine_path: str | None = None,
    memory: RoundMemory | None = None,
    now: float | None = None,
    stop: threading.Event | None = None,
) -> dict:
    """Diagnose every finished game of a human that has no report for its key,
    and draw its exercises.

    Idempotent : a game already reported under the same key is skipped.
    An unreadable registry stops the round (fail closed) ; a game that
    cannot be read or analysed is logged and left for the next round.
    With ``memory`` (``palamede-coach run``), unchanged games are not read
    and a game that waited or failed is ``deferred`` for a while.
    ``stop`` is read between two games : a SIGTERM during the first
    catch-up (a round of 10 to 30 min) ends the round after the game in
    hand, the games left are read at the next start.
    """
    players = registry.load(games_dir)["players"]
    if memory is not None:
        memory.start_round(games_dir)
    now = time.time() if now is None else now
    summary: dict = {"diagnosed": [], "waiting": [], "failed": [], "deferred": [], "exercises": 0}
    pgns = sorted(games_dir.glob("*.pgn"))
    mtimes = {pgn: mtime_ns(pgn) for pgn in pgns}  # before any game is read
    for pgn in pgns:
        if stop is not None and stop.is_set():
            break
        game_id = pgn.stem
        if is_exercise_game(game_id):
            continue  # one move from a position : no game to learn from
        if memory is not None and memory.deferred(game_id, now):
            summary["deferred"].append(game_id)
            continue
        if memory is not None and memory.unchanged(pgn):
            continue
        mtime = mtimes[pgn]  # before reading : a move meanwhile is seen next round
        outcome, added = _one_game(pgn, games_dir, players, depth=depth, engine_path=engine_path)
        if outcome != "settled":
            summary[outcome].append(game_id)
        if isinstance(added, Wait):
            if memory is not None:
                memory.wait_for(pgn, added, mtimes, now)
            continue
        summary["exercises"] += added
        if memory is None:
            continue
        if outcome == "failed":
            memory.retry_later(game_id, now)
        else:
            memory.settle(game_id, mtime)
    return summary


def run_forever(
    games_dir: Path,
    *,
    interval: float = DEFAULT_INTERVAL,
    depth: int = DEFAULT_DEPTH,
    engine_path: str | None = None,
    stop: threading.Event | None = None,
) -> None:
    """``palamede-coach run`` : a round every ``interval`` seconds until ``stop``.

    A round that fails (an unreadable registry, a games directory gone)
    is logged and the next one comes : the coach never stops on its own.
    """
    stop = stop or threading.Event()
    memory = RoundMemory()
    log.info("coach: running on %s every %ss", games_dir, interval)
    while not stop.is_set():
        try:
            summary = run_once(
                games_dir, depth=depth, engine_path=engine_path, memory=memory, stop=stop
            )
        except Exception:  # noqa: BLE001 — logged, and the next round comes
            log.exception("coach: round failed")
        else:
            if any(summary[key] for key in ("diagnosed", "waiting", "failed")):
                log.info("coach: %s", json.dumps(summary))
        stop.wait(interval)
    log.info("coach: stopped")


def main(argv: list[str] | None = None) -> int:
    """``palamede-coach once`` or ``run`` over the games directory."""
    parser = argparse.ArgumentParser(prog="palamede-coach", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    once = sub.add_parser("once", help="diagnose the finished games and draw exercises once")
    run = sub.add_parser("run", help="a round every --interval seconds, until SIGTERM")
    run.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    for command in (once, run):
        command.add_argument("--games-dir", type=Path, default=None)
        command.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    games_dir = args.games_dir or resolve_games_dir()
    if args.command == "run":
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        run_forever(games_dir, interval=args.interval, depth=args.depth, stop=stop)
        return 0
    summary = run_once(games_dir, depth=args.depth)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
