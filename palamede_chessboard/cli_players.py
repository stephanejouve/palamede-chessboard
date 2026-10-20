"""CLI for the player registry (#34) : ``palamede-players``.

Usage ::

    palamede-players add leader --kind agent --name Leader
    palamede-players list
    palamede-players show leader
    palamede-players token leader --label "Claude Code Hera" --out /path/leader.token
    palamede-players revoke leader a1b2c3d4

The games directory comes from ``MCP_CHESSBOARD_GAMES_DIR`` (or
``--games-dir``). A new token is **never** printed by default : it is
written to ``--out`` (created ``0600``, an existing file is refused),
or to stdout only with an explicit ``--stdout``. It cannot be shown
again afterwards — only its id, label and dates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from palamede_chessboard.paths import resolve_games_dir
from palamede_chessboard.players import registry


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="palamede-players", description=__doc__.splitlines()[0])
    parser.add_argument("--games-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="create a player")
    add.add_argument("player")
    add.add_argument("--kind", choices=registry.KINDS, required=True)
    add.add_argument("--name", default="", help="display name (default : the id)")

    sub.add_parser("list", help="list players (no token digests)")

    show = sub.add_parser("show", help="one player and its token ids")
    show.add_argument("player")

    tok = sub.add_parser("token", help="issue a new API token")
    tok.add_argument("player")
    tok.add_argument("--label", required=True, help="where the token is used")
    where = tok.add_mutually_exclusive_group(required=True)
    where.add_argument("--out", type=Path, help="file to create (0600, must not exist)")
    where.add_argument("--stdout", action="store_true", help="print the token (explicit)")

    rev = sub.add_parser("revoke", help="revoke a token by its id")
    rev.add_argument("player")
    rev.add_argument("token_id")
    return parser


def _issue_to_file(games_dir: Path, args: argparse.Namespace) -> None:
    """Create ``--out`` first (``O_EXCL``, ``0600``), then issue the token.

    Review Junior #54 : issuing first left an active token nobody held
    when the file could not be created (missing directory, existing
    file). If the issue itself fails, the empty file is removed.
    """
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(registry.issue_token(games_dir, args.player, label=args.label) + "\n")
    except BaseException:
        args.out.unlink(missing_ok=True)
        raise


def _run(args: argparse.Namespace, games_dir: Path) -> None:
    if args.command == "add":
        print(
            json.dumps(
                registry.add_player(games_dir, args.player, kind=args.kind, display_name=args.name)
            )
        )
    elif args.command == "list":
        for p in registry.list_players(games_dir):
            active = sum(1 for t in p["tokens"] if t["revoked"] is None)
            print(
                f"{p['player_id']:<20} {p['kind']:<6} {active} jeton(s) actif(s)  {p['display_name']}"
            )
    elif args.command == "show":
        players = {p["player_id"]: p for p in registry.list_players(games_dir)}
        player_id = registry.player_id_of(args.player)
        if player_id not in players:
            raise registry.RegistryError(f"unknown player {player_id!r}")
        print(json.dumps(players[player_id], indent=2, ensure_ascii=False))
    elif args.command == "token":
        if args.stdout:
            print(registry.issue_token(games_dir, args.player, label=args.label))
        else:
            _issue_to_file(games_dir, args)
            print(f"jeton écrit dans {args.out} (0600)", file=sys.stderr)
    elif args.command == "revoke":
        registry.revoke_token(games_dir, args.player, args.token_id)
        print(f"jeton {args.token_id} révoqué", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    games_dir = args.games_dir or resolve_games_dir()
    try:
        _run(args, games_dir)
    except (registry.RegistryError, OSError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
