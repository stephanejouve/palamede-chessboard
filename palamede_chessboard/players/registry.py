"""Native player registry (#34, step 1).

palamede (MIT) must work without Nextcloud : players and their API
tokens live in ``<games_dir>/.players/players.json`` (override with
``PALAMEDE_PLAYERS``)::

    {"version": 1, "players": {"leader": {
        "display_name": "Leader", "kind": "agent", "created": "…",
        "tokens": [{"id": "a1b2c3d4", "sha256": "…", "label": "…",
                    "created": "…", "revoked": null}],
        "links": {}}}}

* ``player_id`` is the name written in the PGN headers, normalised by
  :func:`~palamede_chessboard.chess_elo.normalize_identity`.
* Only token **digests** are stored ; the plaintext token is returned
  once by :func:`issue_token` and never again.
* The file is ``0600`` in a ``0700`` directory : digests are not secrets,
  but nobody else needs to read them.
* Unlike the preferences, a missing registry is empty but an
  **unreadable** one raises :class:`RegistryError` : authentication
  must never read a broken file as "no players" or "anyone".

This step adds no enforcement : callers are not checked yet (mode
observation then strict mode come next, #34 §4).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from palamede_chessboard import _atomic
from palamede_chessboard.chess_elo import normalize_identity
from palamede_chessboard.players import tokens

REGISTRY_DIRNAME = ".players"
REGISTRY_FILENAME = "players.json"
REGISTRY_ENV = "PALAMEDE_PLAYERS"
KINDS = ("agent", "human")
_PLAYER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_LABEL_MAX = 64


class RegistryError(Exception):
    """Invalid request, or a registry file that cannot be trusted.

    Deliberately **not** a ``ValueError`` (review Admin #54) : a broad
    ``except ValueError`` around authentication code would otherwise
    swallow an unreadable registry and fail open.
    """


def registry_path(games_dir: Path) -> Path:
    override = os.environ.get(REGISTRY_ENV, "").strip()
    return Path(override) if override else games_dir / REGISTRY_DIRNAME / REGISTRY_FILENAME


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def player_id_of(raw: object) -> str:
    """Normalised, validated player id ; :class:`RegistryError` otherwise."""
    name = normalize_identity(raw) if isinstance(raw, str) else ""
    if not _PLAYER_ID_RE.match(name):
        raise RegistryError(f"invalid player id {raw!r} (a-z, 0-9, _ and -, 32 max)")
    return name


def load(games_dir: Path) -> dict:
    """The registry as a dict ; ``{"version": 1, "players": {}}`` if absent.

    Every other failure — including a ``.players`` directory that cannot
    be traversed — is a :class:`RegistryError` (review Junior #54 : no
    raw ``PermissionError`` for the authentication middleware to miss).
    """
    path = registry_path(games_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "players": {}}
    except (OSError, ValueError) as exc:
        raise RegistryError(f"{path} is unreadable ({exc})") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("players"), dict):
        raise RegistryError(f"{path} has no 'players' object")
    return raw


def _save(games_dir: Path, raw: dict) -> None:
    path = registry_path(games_dir)
    _atomic.write_json(path, raw, mode=0o600)


@contextlib.contextmanager
def _writing(games_dir: Path) -> Iterator[None]:
    """Directory ready, lock held.

    Permissions are tightened **only on the dedicated** ``.players``
    directory : it loses its group / other bits but never gains owner
    bits (a ``.players`` locked to ``0000`` stays locked). With the
    ``PALAMEDE_PLAYERS`` override the parent is any directory — possibly
    the games directory itself — and is left untouched (review Admin
    #54 : the games directory was turned to ``drwx------``). Any
    ``OSError`` surfaces as :class:`RegistryError`.
    """
    path = registry_path(games_dir)
    try:
        if os.environ.get(REGISTRY_ENV, "").strip():
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            mode = stat.S_IMODE(path.parent.stat().st_mode)
            if mode & 0o077:
                os.chmod(path.parent, mode & 0o700)
        with _atomic.locked(path):
            yield
    except OSError as exc:
        raise RegistryError(f"{path} is not writable ({exc})") from exc


def add_player(games_dir: Path, raw_id: object, *, kind: str, display_name: str = "") -> dict:
    """Create a player ; refuses an existing id or an unknown kind."""
    player_id = player_id_of(raw_id)
    if kind not in KINDS:
        raise RegistryError(f"kind must be one of {KINDS}, got {kind!r}")
    with _writing(games_dir):
        raw = load(games_dir)
        if player_id in raw["players"]:
            raise RegistryError(f"player {player_id!r} already exists")
        raw["players"][player_id] = {
            "display_name": display_name.strip() or player_id,
            "kind": kind,
            "created": _now(),
            "tokens": [],
            "links": {},
        }
        _save(games_dir, raw)
    return public_view(player_id, raw["players"][player_id])


def issue_token(games_dir: Path, raw_id: object, *, label: str) -> str:
    """Create a token for ``raw_id`` and return it **in plaintext, once**."""
    player_id = player_id_of(raw_id)
    label = label.strip()
    if not label or len(label) > _LABEL_MAX:
        raise RegistryError(f"label must be 1..{_LABEL_MAX} characters")
    with _writing(games_dir):
        raw = load(games_dir)
        player = raw["players"].get(player_id)
        if player is None:
            raise RegistryError(f"unknown player {player_id!r}")
        taken = {t["id"] for p in raw["players"].values() for t in p.get("tokens", [])}
        token_id, secret = tokens.new_token_id(taken), tokens.new_secret()
        player.setdefault("tokens", []).append(
            {
                "id": token_id,
                "sha256": tokens.digest(secret),
                "label": label,
                "created": _now(),
                "revoked": None,
            }
        )
        _save(games_dir, raw)
    return tokens.format_token(token_id, secret)


def revoke_token(games_dir: Path, raw_id: object, token_id: str) -> None:
    """Mark a token revoked (kept for the audit trail, never reusable)."""
    player_id = player_id_of(raw_id)
    with _writing(games_dir):
        raw = load(games_dir)
        player = raw["players"].get(player_id)
        found = [t for t in (player or {}).get("tokens", []) if t.get("id") == token_id]
        if not found:
            raise RegistryError(f"no token {token_id!r} for player {player_id!r}")
        if found[0].get("revoked") is None:
            found[0]["revoked"] = _now()
            _save(games_dir, raw)


def verify_token(games_dir: Path, token: object) -> str | None:
    """``player_id`` owning a valid, non-revoked ``token`` ; else ``None``.

    Raises :class:`RegistryError` if the registry cannot be read : the
    caller must then refuse, not let the request through.
    """
    parsed = tokens.parse_token(token)
    if parsed is None:
        return None
    token_id, secret = parsed
    for player_id, player in load(games_dir)["players"].items():
        for entry in player.get("tokens", []):
            if entry.get("id") != token_id:
                continue
            ok = entry.get("revoked") is None and tokens.matches(secret, str(entry.get("sha256")))
            return player_id if ok else None
    return None


def public_view(player_id: str, player: dict) -> dict:
    """A player without any token digest — safe to print or return."""
    return {
        "player_id": player_id,
        "display_name": player.get("display_name", player_id),
        "kind": player.get("kind"),
        "created": player.get("created"),
        "tokens": [
            {k: t.get(k) for k in ("id", "label", "created", "revoked")}
            for t in player.get("tokens", [])
        ],
    }


def list_players(games_dir: Path) -> list[dict]:
    return [public_view(pid, p) for pid, p in sorted(load(games_dir)["players"].items())]
