"""Pairing this board to a player, by a short code (#351 §4, #227, RFC 8628).

    palamede-chessnut-pair [--viewer <viewer URL>] [--token-file <path>] [--name <name>]

The board has no token yet : it asks the viewer for a code
(``POST /api/device/code``), prints it with the page to open, then polls
``POST /api/device/token`` at the interval the viewer gives. The player,
signed in to the viewer, types the code on ``/appairer`` and says yes ; the
board then receives its ``device`` token, **once**, and writes it to the
token file, readable by its owner only (``0600``, in a ``0700`` directory ;
under the user profile on Windows), where the bridge reads it.

The token is never printed nor logged. An existing token file is kept
unless ``--replace`` says so : pairing again must not silently throw away
the token in use.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import platform
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from palamede_chessnut import config, viewer
from palamede_chessnut.cli import check_viewer_url

#: The viewer's token form (``players/tokens.py``) : nothing else is written.
TOKEN = re.compile(r"plm_[0-9a-f]{8}_[A-Za-z0-9_-]{43}")
SLOW_DOWN_STEP = 5
DEFAULT_TOKEN_NAME = "token"


def default_token_file() -> Path:
    """``token`` next to the settings file (``~/.config/palamede/token`` on macOS and Linux)."""
    return config.config_path().parent / DEFAULT_TOKEN_NAME


def default_name() -> str:
    """What the player reads on ``/appairer`` : the board and the computer it is plugged in."""
    host = platform.node().split(".")[0]
    return f"Chessnut ({host})" if host else "Chessnut"


def check_token_place(path: Path, *, replace: bool) -> None:
    """Refuse a file that exists (unless ``replace``), or one outside the profile on Windows."""
    if sys.platform == "win32" and not path.absolute().parent.resolve().is_relative_to(
        Path.home().resolve()
    ):
        raise SystemExit(f"{path} : outside your user profile, keep the token under {Path.home()}")
    if path.exists() and not replace:
        raise SystemExit(f"{path} already holds a token : give --replace to pair again over it")


def write_token(path: Path, token: str) -> None:
    """Write ``token`` to ``path``, ``0600``, atomically : never readable by others, even a moment."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def ask_code(viewer_url: str, name: str) -> dict[str, Any]:
    """The viewer's code for this board ; exits with what to do when it gives none."""
    status, body = viewer.post_json_without_token(
        f"{viewer_url}/api/device/code", {"device_name": name}
    )
    if status is None:
        raise SystemExit(f"{viewer_url} does not answer : check the URL, or retry in a moment")
    if status in (404, 405):
        raise SystemExit(f"{viewer_url} is too old to pair a board (0.29.0 or newer needed)")
    if status == 429:
        raise SystemExit("too many pairing requests from this address : retry in a minute")
    keys = ("device_code", "user_code", "verification_uri", "expires_in", "interval")
    if status != 200 or not isinstance(body, dict) or not all(k in body for k in keys):
        raise SystemExit(f"{viewer_url} : unexpected answer to /api/device/code (HTTP {status})")
    return body


def wait_token(
    viewer_url: str,
    asked: dict[str, Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, str]:
    """``(token, account)`` once the player said yes ; exits on a refusal or the code's end."""
    interval = int(asked["interval"])
    deadline = clock() + int(asked["expires_in"])
    url = f"{viewer_url}/api/device/token"
    while clock() < deadline:
        sleep(interval)
        status, body = viewer.post_json_without_token(url, {"device_code": asked["device_code"]})
        error = body.get("error") if isinstance(body, dict) else None
        if status == 200 and isinstance(body, dict):
            token, account = body.get("access_token"), body.get("player")
            if not isinstance(token, str) or not TOKEN.fullmatch(token):
                raise SystemExit(f"{viewer_url} : unexpected token form, nothing written")
            return token, str(account)
        if status == 429 or error == "slow_down":
            interval += SLOW_DOWN_STEP
        elif error == "access_denied":
            raise SystemExit("the pairing was refused on the viewer's page")
        elif error == "expired_token":
            raise SystemExit("the code expired or was already used : run the pairing again")
        # authorization_pending, or the viewer unreachable a moment : asked again.
    raise SystemExit("the code expired before the player said yes : run the pairing again")


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> None:
    first = argparse.ArgumentParser(add_help=False)
    first.add_argument("--config", type=Path)
    known, _rest = first.parse_known_args(argv)
    named = known.config is not None or config.named_by_environment()
    settings = config.load(known.config or config.config_path(), named=named)
    parser = argparse.ArgumentParser(
        description="Pair this board to your account : a code to type on the viewer's page."
    )
    parser.add_argument(
        "--config", type=Path, help=f"settings file (default {config.config_path()})"
    )
    parser.add_argument(
        "--viewer", required="viewer" not in settings, default=settings.get("viewer")
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=settings.get("token_file") or default_token_file(),
        help="where the board's token is written (0600)",
    )
    parser.add_argument("--name", default=default_name(), help="the name shown on the page")
    parser.add_argument(
        "--replace", action="store_true", help="pair again over an existing token file"
    )
    args = parser.parse_args(argv)
    viewer_url = args.viewer.rstrip("/")
    check_viewer_url(viewer_url)
    token_file = args.token_file.expanduser()
    check_token_place(token_file, replace=args.replace)
    asked = ask_code(viewer_url, args.name)
    minutes = max(1, int(asked["expires_in"]) // 60)
    out(f"Open {viewer_url}{asked['verification_uri']}, signed in, and type the code :")
    out(f"    {asked['user_code']}")
    out(f"It is valid {minutes} minutes. Waiting for your yes on the page…")
    token, account = wait_token(viewer_url, asked)
    write_token(token_file, token)
    out(f"Board paired to {account} ; its token is in {token_file} (readable by you only).")
    if "token_file" not in settings:
        out(f'Name it in {config.config_path()} : token_file = "{token_file}"')
