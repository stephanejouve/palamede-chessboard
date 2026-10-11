"""CLI entry for :mod:`palamede_chessboard.chess_matchmaking` (``palamede-matchmaking``).

Chantier ELO volet 3b (post PR #296 ``30+30`` default) : exposes the
pure-function pairing of
:func:`palamede_chessboard.chess_matchmaking.schedule_round_from_disk` as a
one-shot CLI :

* ``chess-matchmaking plan`` — print the next round WITHOUT creating
  the PGN files. Default command, safe to run anytime.
* ``chess-matchmaking create`` — actually create the PGN files for
  the computed round via :func:`chessboard_state.GameState.create`,
  with the default clock from volet 3a (``30+30`` unless
  ``--time-control`` overrides). Prints a summary of what was created.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from palamede_chessboard.chess_matchmaking import (
    RoundSchedule,
    schedule_round_from_disk,
)
from palamede_chessboard.chessboard_clock import init_and_save_clock
from palamede_chessboard.chessboard_state import (
    DEFAULT_TIME_CONTROL_FORMAT,
    GAMES_DIR,
    GameAlreadyExistsError,
    GameMetadata,
    GameState,
    TimeControl,
)


def _infer_next_round(games_dir: Path | None) -> int:
    """Return ``max(Round header) + 1`` across all PGNs ; 1 if none / unparseable.

    CD review 2026-10-06 PR #297 mineur : ``--round`` shouldn't be a
    hardcoded ``1`` default — if PGNs already exist with ``Round "N"``,
    the next round should be ``N+1``. Non-integer Round values (``"-"``,
    ``"irregular"``) are ignored (treated as 0) rather than crashing
    the CLI.
    """
    import chess.pgn

    gdir = games_dir or GAMES_DIR
    if not gdir.is_dir():
        return 1
    max_round = 0
    for pgn in gdir.glob("*.pgn"):
        try:
            with pgn.open("r", encoding="utf-8") as f:
                game = chess.pgn.read_headers(f)
            if game is None:
                continue
            raw = game.get("Round", "")
            try:
                n = int(raw)
            except (ValueError, TypeError):
                continue
            if n > max_round:
                max_round = n
        except OSError:
            continue
    return max_round + 1


def _format_schedule_table(schedule: RoundSchedule) -> str:
    rows = ["Next round schedule :", "-" * 50]
    if not schedule.matches:
        rows.append("  (no match — fewer than two agents available)")
    for m in schedule.matches:
        rows.append(
            f"  {m.white:16} ({m.white_rating:4}) vs "
            f"{m.black:16} ({m.black_rating:4})  Δ={m.elo_gap}"
        )
    if schedule.bye:
        rows.append(f"\n  bye : {schedule.bye} (fewest rated games)")
    if schedule.excluded_busy:
        rows.append(f"\n  excluded (game in progress) : {', '.join(schedule.excluded_busy)}")
    return "\n".join(rows)


def _default_game_id(white: str, black: str, round_num: int) -> str:
    """Build a deterministic game_id ``<white>-vs-<black>-rN-YYYYMMDD``.

    The round number and date keep two matches of the same pair
    distinct across days (no collision on retry).
    """
    today = datetime.now(UTC).strftime("%Y%m%d")
    return f"{white}-vs-{black}-r{round_num}-{today}"


def _cmd_plan(args: argparse.Namespace) -> int:
    schedule = schedule_round_from_disk(games_dir=args.games_dir)
    if args.json:
        print(json.dumps(schedule.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_schedule_table(schedule))
    return 0


def _cmd_create(args: argparse.Namespace) -> int:
    schedule = schedule_round_from_disk(games_dir=args.games_dir)
    if not schedule.matches:
        print("No match to create — fewer than two agents available.", file=sys.stderr)
        return 1
    round_num = args.round if args.round is not None else _infer_next_round(args.games_dir)
    tc = TimeControl(format=args.time_control)
    created: list[dict] = []
    for m in schedule.matches:
        game_id = _default_game_id(m.white, m.black, round_num)
        meta = GameMetadata(
            game_id=game_id,
            event=args.event,
            site=args.site,
            round=str(round_num),
            white=m.white,
            black=m.black,
            time_control=tc,
            rated=True,
        )
        try:
            state = GameState.create(meta, games_dir=args.games_dir)
        except GameAlreadyExistsError:
            print(f"  SKIP  {game_id} (already exists)", file=sys.stderr)
            continue
        # CD review PR #297 bloquant 2 : init + persist the clock side-car
        # via the shared helper (same code path as chess_create_game MCP).
        clock = init_and_save_clock(state.metadata.game_id, tc, games_dir=args.games_dir)
        created.append(
            {
                "game_id": game_id,
                "white": m.white,
                "black": m.black,
                "time_control": args.time_control,
                "clock_initialized": clock is not None,
            }
        )
        clock_tag = "clock" if clock is not None else "no-clock"
        print(
            f"  CREATED  {game_id}  ({m.white} vs {m.black}, " f"{args.time_control}, {clock_tag})"
        )
    if args.json:
        print(
            json.dumps(
                {"created": created, "bye": schedule.bye, "round": round_num},
                indent=2,
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chess-matchmaking",
        description=(
            "Compute (and optionally create) the next tournament round "
            "based on the current Elo board and games-in-progress."
        ),
    )
    parser.add_argument(
        "--games-dir",
        type=Path,
        default=None,
        help="Override the games directory (default : chess_elo.GAMES_DIR).",
    )
    sub = parser.add_subparsers(dest="cmd")

    plan = sub.add_parser("plan", help="Preview the next round (no file created).")
    plan.add_argument("--json", action="store_true", help="JSON output.")
    plan.set_defaults(func=_cmd_plan)

    create = sub.add_parser("create", help="Create the PGN files for the next round.")
    create.add_argument(
        "--time-control",
        default=DEFAULT_TIME_CONTROL_FORMAT,
        help=f"Clock format (default : {DEFAULT_TIME_CONTROL_FORMAT}).",
    )
    create.add_argument(
        "--round",
        type=int,
        default=None,
        help=(
            "Round number for the PGN header + game_id suffix. If omitted, "
            "inferred as max(Round header in games_dir) + 1."
        ),
    )
    create.add_argument(
        "--event",
        default="Tournoi à 4",
        help='PGN "Event" header (default : "Tournoi à 4").',
    )
    create.add_argument(
        "--site",
        default="Chas, Tiresias",
        help='PGN "Site" header (default : "Chas, Tiresias").',
    )
    create.add_argument("--json", action="store_true", help="JSON summary.")
    create.set_defaults(func=_cmd_create)

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        # No subcommand → default to `plan` (safe read-only preview).
        return _cmd_plan(argparse.Namespace(games_dir=args.games_dir, json=False))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
