"""Command line of the bot.

    palamede-maia --viewer http://127.0.0.1:8766 --tokens-dir /run/secrets/maia \\
        --weights-dir /opt/maia/weights --humans stephane,cilou

The tokens directory holds one file per account, ``bot-maia-<elo>.token``,
readable by its owner only ; the levels played are the files present. A
token is never logged. ``--humans`` lists the accounts Maia may play
against : nobody when it is empty. Maia only plays games opened by that
human through Tablier (``tablier.<human>.*``), plus those named in
``--games`` (empty by default).
"""

from __future__ import annotations

import argparse
import logging
import os
import stat
import sys
import time
from pathlib import Path

from palamede_maia.bot import Bot, level_of
from palamede_maia.engine import Engines

log = logging.getLogger("palamede_maia")


def read_tokens(directory: Path) -> dict[str, str]:
    """``{account: token}`` of the ``bot-maia-<elo>.token`` files ; others ignored."""
    tokens = {}
    for path in sorted(directory.glob("bot-maia-*.token")):
        account = path.name.removesuffix(".token")
        if level_of(account) is None:
            log.warning("%s : not a Maia level, ignored", path.name)
            continue
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            log.warning("%s : readable by others, ignored (chmod 600)", path.name)
            continue
        token = path.read_text(encoding="utf-8").strip()
        if token:
            tokens[account] = token
    return tokens


def parse_humans(raw: str) -> frozenset[str]:
    return frozenset(name.strip().lower() for name in raw.split(",") if name.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maia bot for palamède (#93)")
    parser.add_argument(
        "--viewer", default=os.environ.get("PALAMEDE_VIEWER", "http://127.0.0.1:8766")
    )
    parser.add_argument(
        "--tokens-dir",
        type=Path,
        default=Path(os.environ.get("MAIA_TOKENS_DIR", "/run/secrets/maia")),
    )
    parser.add_argument(
        "--weights-dir",
        type=Path,
        default=Path(os.environ.get("MAIA_WEIGHTS_DIR", "/opt/maia/weights")),
    )
    parser.add_argument("--lc0", default=os.environ.get("MAIA_LC0", "lc0"))
    parser.add_argument("--humans", default=os.environ.get("MAIA_HUMANS", ""))
    parser.add_argument(
        "--games",
        default=os.environ.get("MAIA_GAMES", ""),
        help="games other than tablier.<human>.* that Maia may play (empty by default)",
    )
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between rounds")
    parser.add_argument("--once", action="store_true", help="one round, then exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tokens = read_tokens(args.tokens_dir)
    humans = parse_humans(args.humans)
    log.info(
        "levels : %s ; humans : %s",
        ", ".join(sorted(tokens)) or "none",
        ", ".join(sorted(humans)) or "none",
    )
    if not tokens:
        log.error("no bot-maia-<elo>.token in %s : nothing to play", args.tokens_dir)
        return 2

    engines = Engines(args.lc0, args.weights_dir)
    games = frozenset(g.strip() for g in args.games.split(",") if g.strip())
    bot = Bot(args.viewer.rstrip("/"), tokens, humans, engines.move, games)
    try:
        while True:
            bot.play_round()
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        engines.close()


if __name__ == "__main__":
    sys.exit(main())
