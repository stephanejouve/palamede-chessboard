"""CLI entry for the chess Elo tooling (``chess-elo``).

Two commands :

* ``chess-elo show`` — print the current rating board (reads the cache if
  present, otherwise rebuilds from PGN).
* ``chess-elo rebuild`` — force a fresh rebuild from PGNs and overwrite
  the cache. Idempotent — safe to run as often as needed.

Both commands target ``/Users/Shared/games/`` by default ; pass
``--games-dir`` to point at a test fixture.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from palamede_chessboard.chess_elo import (
    load_cached_ratings,
    ratings_path,
    rebuild_and_persist_cache,
)
from palamede_chessboard.chessboard_state import GAMES_DIR


def _format_board_table(board) -> str:
    if not board.players:
        return "No rated games yet."
    rows = sorted(
        board.players.items(),
        key=lambda kv: kv[1].rating,
        reverse=True,
    )
    width_name = max(len(name) for name, _ in rows)
    width_name = max(width_name, len("player"))
    header = f"{'player'.ljust(width_name)}  rating  games"
    sep = "-" * len(header)
    lines = [header, sep]
    for name, pr in rows:
        lines.append(f"{name.ljust(width_name)}  {pr.rating:>6}  {pr.games:>5}")
    return "\n".join(lines)


def _cmd_show(args: argparse.Namespace) -> int:
    board = load_cached_ratings(games_dir=args.games_dir)
    if board is None:
        print("(no cache yet, rebuilding from PGN)", file=sys.stderr)
        # Persist the fresh board with source_mtime+source_pgns so a
        # subsequent `chess-elo show` or viewer poll hits the cache.
        board, _ = rebuild_and_persist_cache(games_dir=args.games_dir)
    if args.json:
        print(json.dumps(board.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_board_table(board))
    return 0


def _cmd_rebuild(args: argparse.Namespace) -> int:
    # CD review 2026-10-05 PR #295 (cycle 3 non-blocking) : route via
    # rebuild_and_persist_cache so the stored source_mtime+source_pgns
    # match what the viewer /api/elo expects. Previous path left those
    # fields unset in the cache, forcing one extra rebuild in the viewer
    # on next poll.
    board, saved = rebuild_and_persist_cache(games_dir=args.games_dir)
    path = ratings_path(args.games_dir)
    status = "cached at" if saved else "NOT cached (write failed), fresh board at"
    print(
        f"Rebuilt ratings for {len(board.players)} player(s) from "
        f"{len(board.history)} game(s). {status}: {path}"
    )
    if args.show:
        print()
        print(_format_board_table(board))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chess-elo",
        description="Compute Elo ratings for the agent chess tournament.",
    )
    parser.add_argument(
        "--games-dir",
        type=Path,
        default=GAMES_DIR,
        help=f"PGN directory (default: {GAMES_DIR}).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="Print the current rating board.")
    show.add_argument("--json", action="store_true", help="Output raw JSON.")
    show.set_defaults(func=_cmd_show)

    rebuild = sub.add_parser(
        "rebuild",
        help="Recompute from PGNs and overwrite the cache.",
    )
    rebuild.add_argument(
        "--show",
        action="store_true",
        help="Print the resulting board after rebuilding.",
    )
    rebuild.set_defaults(func=_cmd_rebuild)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
