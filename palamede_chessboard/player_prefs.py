"""Per-player preferences (issue #38) — ``max_concurrent`` for now.

Stored in ``<games_dir>/.player_prefs.json`` (override with the
``PALAMEDE_PLAYER_PREFS`` env var), never in the PGNs::

    {"players": {"leader": {"max_concurrent": 2}}}

``max_concurrent`` is the number of **rated** games a player accepts to
have in progress at the same time. The matchmaking treats a player as
busy only once that ceiling is reached ; unrated games (``rated=false``,
#33) never count. Default 1, which keeps the historical behaviour.

Reading is fail-safe : a missing, unreadable or malformed file yields
no preferences, i.e. the default ceiling of 1 for everyone — the most
conservative pairing. Writing is atomic (tmp + replace) and validated.

Interim storage until the identity registry of #34 lands : the setter
does not authenticate the caller yet (same trust level as the ``by``
audit field of ``chess_play_move``).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import tempfile
from pathlib import Path

from palamede_chessboard.chess_elo import normalize_identity

log = logging.getLogger(__name__)

PREFS_FILENAME = ".player_prefs.json"
PREFS_ENV = "PALAMEDE_PLAYER_PREFS"
DEFAULT_MAX_CONCURRENT = 1
#: Upper bound, so a typo (``200`` for ``2``) cannot silently remove a
#: player from every busy check.
MAX_CONCURRENT_LIMIT = 10


class PlayerPrefsError(ValueError):
    """Invalid preference value or player name."""


def prefs_path(games_dir: Path) -> Path:
    override = os.environ.get(PREFS_ENV, "").strip()
    return Path(override) if override else games_dir / PREFS_FILENAME


def load_prefs(games_dir: Path) -> dict[str, dict]:
    """Return ``{player: {pref: value}}``, or ``{}`` if absent or invalid."""
    path = prefs_path(games_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("player prefs unreadable at %s (%s) : defaults apply", path, exc)
        return {}
    players = raw.get("players") if isinstance(raw, dict) else None
    if not isinstance(players, dict):
        log.warning("player prefs at %s has no 'players' object : defaults apply", path)
        return {}
    return {str(k): v for k, v in players.items() if isinstance(v, dict)}


def _valid_ceiling(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= MAX_CONCURRENT_LIMIT
    )


def max_concurrent_for(prefs: dict[str, dict], player: str) -> int:
    """Ceiling of rated games in progress for ``player`` (default 1).

    An out-of-range or non-integer stored value falls back to the
    default rather than widening the ceiling.
    """
    value = prefs.get(normalize_identity(player), {}).get("max_concurrent")
    if isinstance(value, int) and _valid_ceiling(value):
        return value
    return DEFAULT_MAX_CONCURRENT


@contextlib.contextmanager
def _locked(path: Path):
    """Exclusive ``flock`` on ``<prefs>.lock`` for the read-modify-write."""
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_for_update(path: Path) -> dict:
    """Strict read before a write (review Junior #40).

    Unlike :func:`load_prefs`, an existing but unreadable or malformed
    file is an error : rewriting it from ``{}`` would silently drop the
    other players' preferences. A missing file starts empty. Unknown
    keys are preserved as-is.
    """
    if not path.exists():
        return {"players": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlayerPrefsError(f"{path} is unreadable ({exc}) : nothing written") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("players"), dict):
        raise PlayerPrefsError(f"{path} has no 'players' object : nothing written")
    return raw


def set_max_concurrent(games_dir: Path, player: object, value: object) -> dict:
    """Validate then persist ``max_concurrent`` for ``player``.

    Strict integer in ``1..MAX_CONCURRENT_LIMIT`` (a bool or a string
    such as ``"2"`` is refused). Other players and other keys are kept ;
    an existing but unreadable file is refused rather than overwritten.
    """
    name = normalize_identity(player) if isinstance(player, str) else ""
    if not name:
        raise PlayerPrefsError(f"invalid player {player!r}")
    if not _valid_ceiling(value):
        raise PlayerPrefsError(
            f"max_concurrent must be an integer in 1..{MAX_CONCURRENT_LIMIT}, got {value!r}"
        )
    path = prefs_path(games_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Read-modify-write under an exclusive lock, so two concurrent sets
    # cannot drop each other's update (review Junior #40).
    with _locked(path):
        raw = _read_for_update(path)
        entry = raw["players"].get(name)
        if not isinstance(entry, dict):
            entry = raw["players"][name] = {}
        entry["max_concurrent"] = value
        # mkstemp creates 0600 : keep the existing file's mode (0644 if new).
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2, sort_keys=True)
            os.chmod(tmp_name, mode)
            os.replace(tmp_name, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_name)
            raise
    return {"player": name, "max_concurrent": value}
