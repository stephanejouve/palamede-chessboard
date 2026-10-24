"""Command line of the bridge.

    palamede-chessnut --viewer <viewer URL> --game <game_id> --color white

The player token (the one behind the viewer's 🔑 button) is read from
``--token-file`` (readable by its owner only) or ``PALAMEDE_PLAYER_TOKEN`` ;
it is never logged. The bridge exits by itself once the game is over and
its final position stands on the board, LEDs off.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

import chess

from palamede_chessnut.bridge import Bridge


def check_viewer_url(url: str) -> None:
    """Refuse a URL that would send the player token in clear over the network."""
    parts = urlsplit(url)
    if parts.scheme == "https" or (
        parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")
    ):
        return
    raise SystemExit(f"{url} : https required (http only on loopback), the player token is sent")


def read_token(token_file: Path | None) -> str:
    if token_file is not None:
        if token_file.stat().st_mode & 0o077:
            raise SystemExit(f"{token_file} : readable by other accounts, chmod 600 it first")
        return token_file.read_text(encoding="utf-8").strip()
    token = os.environ.get("PALAMEDE_PLAYER_TOKEN", "").strip()
    if not token:
        raise SystemExit("player token missing : PALAMEDE_PLAYER_TOKEN or --token-file")
    return token


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Play a viewer game on a Chessnut Air+ board.")
    parser.add_argument("--viewer", required=True, help="viewer base URL")
    parser.add_argument("--game", required=True, help="game_id")
    parser.add_argument("--color", required=True, choices=["white", "black"])
    parser.add_argument("--token-file", type=Path, help="file holding the player token")
    args = parser.parse_args(argv)
    check_viewer_url(args.viewer)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    player = chess.WHITE if args.color == "white" else chess.BLACK
    bridge = Bridge(args.viewer, args.game, player, read_token(args.token_file))
    try:
        asyncio.run(bridge.run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        return
    if not bridge.finished:
        raise SystemExit(f"game {args.game} cannot be followed, see the log above")
