"""Per-user defaults of the bridge and the app (#90) : a short command line.

``config.toml``, in ``~/.config/palamede/`` (``$XDG_CONFIG_HOME`` if set)
on macOS and Linux, ``%APPDATA%\\palamede\\`` on Windows ; ``--config`` or
``PALAMEDE_CONFIG`` name another file. For instance ::

    viewer = "https://chess.example"
    token_file = "~/.config/palamede/token"
    transport = "auto"

It names the token *file*, never the token : a ``token`` key is refused.
Options given on the command line win over the file. The file chooses
where the token is sent : one that another account could change is
refused (review Admin #124), see :func:`check_rights`.
"""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TRANSPORTS = ("auto", "ble", "usb")
#: Key → what it must be ; the text is the error.
KEYS = {
    "viewer": "the viewer's base URL, a string",
    "token_file": "the path of the file holding the player token, a string",
    "transport": f"one of {', '.join(TRANSPORTS)}",
}


def config_path() -> Path:
    """Where the file is looked for when ``--config`` does not say."""
    named = os.environ.get("PALAMEDE_CONFIG", "").strip()
    if named:
        return Path(named).expanduser()
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "palamede" / "config.toml"
    base = os.environ.get("XDG_CONFIG_HOME", "").strip() or Path.home() / ".config"
    return Path(base) / "palamede" / "config.toml"


def named_by_environment() -> bool:
    return bool(os.environ.get("PALAMEDE_CONFIG", "").strip())


def check_rights(path: Path) -> None:
    """Refuse a file that another account could change : it would send the token elsewhere.

    POSIX : neither the file nor its directory writable by group or others.
    Windows reports 0o666 whatever the NTFS ACL says : the file must be under
    the user profile, as the token file (``cli.read_token``).
    """
    real = path.resolve()
    if sys.platform == "win32":
        if not real.is_relative_to(Path.home().resolve()):
            raise SystemExit(f"{path} : outside your user profile, keep it under {Path.home()}")
        return
    for place in (real, real.parent):
        if place.stat().st_mode & 0o022:
            raise SystemExit(
                f"{place} : writable by other accounts, who could send your token "
                "elsewhere ; chmod go-w it first"
            )


def load(path: Path, *, named: bool = False) -> dict[str, Any]:
    """The file's settings, checked ; ``{}`` when it does not exist.

    ``named`` : given by ``--config`` or ``PALAMEDE_CONFIG``, it must exist
    (review Admin #124). ``token_file`` comes back as a :class:`~pathlib.Path`,
    ``~`` expanded, and relative to the file's own directory when not absolute.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if named:
            raise SystemExit(f"{path} : no such settings file") from None
        return {}
    except OSError as exc:
        raise SystemExit(f"{path} : unreadable ({exc.strerror or exc})") from None
    check_rights(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path} : not valid TOML ({exc})") from None
    if "token" in data:
        raise SystemExit(f"{path} : never the token itself here, name its file with token_file")
    settings: dict[str, Any] = {}
    for key, value in data.items():
        if key not in KEYS:
            # A newer version may know it : said, not fatal.
            log.warning("%s : unknown setting %r, ignored", path, key)
            continue
        if not isinstance(value, str) or not value.strip():
            raise SystemExit(f"{path} : {key} must be {KEYS[key]}")
        settings[key] = value.strip()
    if settings.get("transport", "auto") not in TRANSPORTS:
        raise SystemExit(f"{path} : transport must be {KEYS['transport']}")
    if "token_file" in settings:
        token_file = Path(settings["token_file"]).expanduser()
        settings["token_file"] = (
            token_file if token_file.is_absolute() else path.parent / token_file
        )
    return settings
