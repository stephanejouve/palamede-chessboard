"""Native player registry (#34, step 1).

palamede (MIT) must work without Nextcloud : players and their API
tokens live in ``<games_dir>/.players/players.json`` (override with
``PALAMEDE_PLAYERS``)::

    {"version": 1, "players": {"leader": {
        "display_name": "Leader", "kind": "agent", "created": "…",
        "tokens": [{"id": "a1b2c3d4", "sha256": "…", "label": "…",
                    "class": "api", "created": "…", "last_used": "…",
                    "revoked": null}],
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

Each token has a ``class`` (#351) : what it may be used for. A token
written before the classes has none and is read as ``api``.

* ``api``, ``browser``, ``device`` : the account's own tokens, accepted by
  :func:`verify_token` everywhere a player acts.
* ``guest`` (one game) and ``recovery`` (opens a browser session only) :
  never accepted by :func:`verify_token` ; their own routes ask
  :func:`verify` for them by name. Fail-closed : a route that forgets them
  refuses them.

``last_used`` is a date, without the hour nor any address : the least
connection data that tells an active token from a forgotten one. A
``browser`` token unused for :data:`BROWSER_IDLE_DAYS` days expires
(owner, 14/11).

This step adds no enforcement : callers are not checked yet (mode
observation then strict mode come next, #34 §4).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from palamede_chessboard import _atomic
from palamede_chessboard.chess_elo import normalize_identity
from palamede_chessboard.players import tokens

REGISTRY_DIRNAME = ".players"
REGISTRY_FILENAME = "players.json"
REGISTRY_ENV = "PALAMEDE_PLAYERS"
KINDS = ("agent", "human")
#: Token classes (#351) ; a token without one is ``api``.
CLASSES = ("api", "browser", "device", "guest", "recovery")
DEFAULT_CLASS = "api"
#: The classes :func:`verify_token` accepts : the account's own tokens.
ACCOUNT_CLASSES = frozenset({"api", "browser", "device"})
#: A ``browser`` token unused this many days no longer verifies (owner, 14/11).
BROWSER_IDLE_DAYS = 90
_PLAYER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_LABEL_MAX = 64


class RegistryError(Exception):
    """Invalid request, or a registry file that cannot be trusted.

    Deliberately **not** a ``ValueError`` (review Admin #54) : a broad
    ``except ValueError`` around authentication code would otherwise
    swallow an unreadable registry and fail open.
    """


class PlayerExists(RegistryError):
    """The id is already a player's : the request, not the file, is wrong."""


def registry_path(games_dir: Path) -> Path:
    override = os.environ.get(REGISTRY_ENV, "").strip()
    return Path(override) if override else games_dir / REGISTRY_DIRNAME / REGISTRY_FILENAME


log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _today() -> date:
    return datetime.now(UTC).date()


def token_class(entry: dict) -> str:
    """The token's class ; ``api`` for a token written before the classes."""
    value = entry.get("class")
    return value if value in CLASSES else DEFAULT_CLASS


@dataclass(frozen=True)
class TokenUse:
    """Who a verified token is : its player, its id and its class."""

    player_id: str
    token_id: str
    token_class: str


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
            raise PlayerExists(f"player {player_id!r} already exists")
        raw["players"][player_id] = {
            "display_name": display_name.strip() or player_id,
            "kind": kind,
            "created": _now(),
            "tokens": [],
            "links": {},
        }
        _save(games_dir, raw)
    return public_view(player_id, raw["players"][player_id])


def _token_entry(raw: dict, label: str, token_class: str) -> tuple[dict, str]:
    """A new token's registry entry, and the token in plaintext."""
    label = label.strip()
    if not label or len(label) > _LABEL_MAX:
        raise RegistryError(f"label must be 1..{_LABEL_MAX} characters")
    if token_class not in CLASSES:
        raise RegistryError(f"class must be one of {CLASSES}, got {token_class!r}")
    taken = {t["id"] for p in raw["players"].values() for t in p.get("tokens", [])}
    token_id, secret = tokens.new_token_id(taken), tokens.new_secret()
    entry = {
        "id": token_id,
        "sha256": tokens.digest(secret),
        "label": label,
        "class": token_class,
        "created": _now(),
        "last_used": None,
        "revoked": None,
    }
    return entry, tokens.format_token(token_id, secret)


def issue_token(
    games_dir: Path, raw_id: object, *, label: str, token_class: str = DEFAULT_CLASS
) -> str:
    """Create a token for ``raw_id`` and return it **in plaintext, once**."""
    player_id = player_id_of(raw_id)
    with _writing(games_dir):
        raw = load(games_dir)
        player = raw["players"].get(player_id)
        if player is None:
            raise RegistryError(f"unknown player {player_id!r}")
        entry, token = _token_entry(raw, label, token_class)
        player.setdefault("tokens", []).append(entry)
        _save(games_dir, raw)
    return token


def add_player_with_tokens(
    games_dir: Path,
    raw_id: object,
    *,
    kind: str,
    display_name: str = "",
    issued: list[tuple[str, str]],
) -> list[str]:
    """Create a player and its tokens ``(label, class)`` in **one** write.

    The tokens in plaintext, in the order asked. A failure leaves no
    account behind : no player without the token that lets them in.
    """
    player_id = player_id_of(raw_id)
    if kind not in KINDS:
        raise RegistryError(f"kind must be one of {KINDS}, got {kind!r}")
    with _writing(games_dir):
        raw = load(games_dir)
        if player_id in raw["players"]:
            raise PlayerExists(f"player {player_id!r} already exists")
        player = {
            "display_name": display_name.strip() or player_id,
            "kind": kind,
            "created": _now(),
            "tokens": [],
            "links": {},
        }
        raw["players"][player_id] = player
        plaintext = []
        for label, token_class in issued:
            entry, token = _token_entry(raw, label, token_class)
            player["tokens"].append(entry)
            plaintext.append(token)
        _save(games_dir, raw)
    return plaintext


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


def _idle(entry: dict, today: date) -> bool:
    """A ``browser`` token unused for :data:`BROWSER_IDLE_DAYS` days."""
    if token_class(entry) != "browser":
        return False
    seen = entry.get("last_used") or str(entry.get("created", ""))[:10]
    try:
        last = date.fromisoformat(seen)
    except ValueError:
        return True  # no readable date : expired rather than eternal
    return today - last > timedelta(days=BROWSER_IDLE_DAYS)


def _mark_used(games_dir: Path, token_id: str, today: str) -> None:
    """Write ``last_used`` once a day per token ; a failure never refuses the token."""
    try:
        with _writing(games_dir):
            raw = load(games_dir)
            for player in raw["players"].values():
                for entry in player.get("tokens", []):
                    if entry.get("id") == token_id and entry.get("last_used") != today:
                        entry["last_used"] = today
                        _save(games_dir, raw)
                        return
    except RegistryError as exc:
        log.warning("last_used of token %s not written (%s)", token_id, exc)


def verify(games_dir: Path, token: object, classes: frozenset[str]) -> TokenUse | None:
    """The :class:`TokenUse` of a valid ``token`` of one of ``classes`` ; else ``None``.

    Valid : well formed, known, not revoked, the right secret, a class in
    ``classes``, and for a ``browser`` token, used in the last
    :data:`BROWSER_IDLE_DAYS` days. The same ``None`` whatever the reason.
    Raises :class:`RegistryError` if the registry cannot be read : the
    caller must then refuse, not let the request through.
    """
    parsed = tokens.parse_token(token)
    if parsed is None:
        return None
    token_id, secret = parsed
    today = _today()
    for player_id, player in load(games_dir)["players"].items():
        for entry in player.get("tokens", []):
            if entry.get("id") != token_id:
                continue
            ok = (
                entry.get("revoked") is None
                and tokens.matches(secret, str(entry.get("sha256")))
                and token_class(entry) in classes
                and not _idle(entry, today)
            )
            if not ok:
                return None
            if entry.get("last_used") != today.isoformat():
                _mark_used(games_dir, token_id, today.isoformat())
            return TokenUse(player_id, token_id, token_class(entry))
    return None


def verify_token(games_dir: Path, token: object) -> str | None:
    """``player_id`` owning a valid ``token`` of the account's classes ; else ``None``.

    ``guest`` and ``recovery`` tokens are refused here (#351) : see :func:`verify`.
    Raises :class:`RegistryError` if the registry cannot be read : the
    caller must then refuse, not let the request through.
    """
    use = verify(games_dir, token, ACCOUNT_CLASSES)
    return use.player_id if use else None


def public_view(player_id: str, player: dict) -> dict:
    """A player without any token digest — safe to print or return."""
    return {
        "player_id": player_id,
        "display_name": player.get("display_name", player_id),
        "kind": player.get("kind"),
        "created": player.get("created"),
        "tokens": [public_token(t) for t in player.get("tokens", [])],
    }


def public_token(entry: dict) -> dict:
    """A token without its digest : id, label, class, dates, revocation."""
    view = {k: entry.get(k) for k in ("id", "label", "created", "last_used", "revoked")}
    view["class"] = token_class(entry)
    return view


def tokens_of(games_dir: Path, player_id: str) -> list[dict]:
    """``player_id``'s tokens, without digests ; ``[]`` for an unknown player."""
    player = load(games_dir)["players"].get(player_id) or {}
    return [public_token(t) for t in player.get("tokens", [])]


def list_players(games_dir: Path) -> list[dict]:
    return [public_view(pid, p) for pid, p in sorted(load(games_dir)["players"].items())]
