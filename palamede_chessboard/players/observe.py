"""Observation mode of player authentication (#34, step 2).

With ``PALAMEDE_AUTH_MODE=observe``, every MCP tool call is checked
against the authorisation rules of #34 §2 and the verdict is appended
to ``<registry dir>/auth_observations.jsonl`` (``0600``). **Nothing is
refused** and the answer to the client is unchanged : the journal shows
what strict mode (step 3) would refuse, before anyone is locked out.

Identity comes **only** from the ``Authorization: Bearer plm_…`` token
(rule of #34 : never from ``by``, never from the source address — the
DSM proxy, Home Assistant and Vacuula all share ``127.0.0.1``). ``by``
is kept for the audit and compared to the token's identity.

The token itself is never written anywhere. An error while observing
is logged and swallowed : observation must never break a game.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from palamede_chessboard import _atomic
from palamede_chessboard.chess_elo import normalize_identity
from palamede_chessboard.players import registry

log = logging.getLogger(__name__)

AUTH_MODE_ENV = "PALAMEDE_AUTH_MODE"
OBSERVATIONS_FILENAME = "auth_observations.jsonl"

#: Tool → who may call it, per #34 §2. Tools not listed are reads :
#: any authenticated caller.
_TURN = "player_to_move"
_PLAYERS = "players"
_CREATOR = "creator"
_COMMENTER = "commenter"
_CLOCK = "players_or_arbiter"
_OWN_ACCOUNT = "own_account"
RULES: dict[str, str] = {
    "chess_play_move": _TURN,
    "chess_resign": _PLAYERS,
    "chess_offer_draw": _PLAYERS,
    "chess_agree_draw": _PLAYERS,
    "chess_decline_draw": _PLAYERS,
    "chess_create_game": _CREATOR,
    "chess_post_commentary": _COMMENTER,
    "chess_clock_start": _CLOCK,
    "chess_set_player_preferences": _OWN_ACCOUNT,
}


def auth_mode() -> str:
    """``observe`` or ``off`` (default, and for any unknown value)."""
    mode = os.environ.get(AUTH_MODE_ENV, "off").strip().lower()
    if mode not in ("off", "observe"):
        log.warning("%s=%r is not supported yet : treated as off", AUTH_MODE_ENV, mode)
        return "off"
    return mode


def bearer_token(headers: Any) -> str | None:
    """The token of an ``Authorization: Bearer …`` header, else ``None``."""
    value = headers.get("authorization") if headers is not None else None
    if not value:
        return None
    scheme, _, token = str(value).partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


def _norm(name: object) -> str:
    return normalize_identity(name) if isinstance(name, str) else ""


def _game_roles(game_id: object, games_dir: Path) -> dict | None:
    """Players, arbiter, spectators and player to move ; ``None`` if no game."""
    from palamede_chessboard.chessboard_state import GameState
    from palamede_chessboard.errors import ChessboardError

    if not isinstance(game_id, str):
        return None
    try:
        state = GameState.load(game_id, games_dir=games_dir)
    except (ChessboardError, OSError, ValueError):
        return None
    return roles_of(state)


def roles_of(state: Any) -> dict:
    """Roles of an **already loaded** game : no second read, so a caller
    that checks then plays acts on the very position it checked."""
    meta = state.metadata
    white, black = _norm(meta.white), _norm(meta.black)
    return {
        "white": white,
        "black": black,
        "players": {white, black},
        "arbiter": _norm(meta.arbiter),
        "spectators": {_norm(s) for s in meta.spectators},
        "to_move": white if state.board.turn else black,
    }


def _by_player(by: object, roles: dict | None) -> str:
    """``by`` as a player id : ``white`` / ``black`` resolve through the PGN."""
    if not isinstance(by, str) or not by.strip():
        return ""
    side = by.strip().lower()
    if roles is not None and side in ("white", "black"):
        return str(roles[side])
    return _norm(by)


def evaluate(tool: str, arguments: dict, who: str | None, roles: dict | None) -> str | None:
    """Refusal code strict mode would return, or ``None`` if allowed.

    ``roles`` : see :func:`_game_roles` ; ``None`` when the call names no
    existing game (the handler then answers GAME_NOT_FOUND by itself).
    """
    if who is None:
        return "UNAUTHENTICATED"
    rule = RULES.get(tool)
    if rule is None:
        return None
    if rule == _OWN_ACCOUNT:
        return None if _norm(arguments.get("player")) == who else "NOT_YOUR_ACCOUNT"
    if rule == _CREATOR:
        allowed = {_norm(arguments.get(k)) for k in ("white", "black", "arbiter")}
        return None if who in allowed else "NOT_A_PLAYER"
    if roles is None:
        return None
    if rule == _TURN:
        if who == roles["to_move"]:
            return None
        return "NOT_YOUR_TURN" if who in roles["players"] else "NOT_A_PLAYER"
    if rule == _PLAYERS:
        return None if who in roles["players"] else "NOT_A_PLAYER"
    if rule == _CLOCK:
        return None if who in roles["players"] | {roles["arbiter"]} else "NOT_A_PLAYER"
    allowed = roles["players"] | {roles["arbiter"]} | roles["spectators"]
    return None if who in allowed else "NOT_ALLOWED"


def _identify(token: str | None, games_dir: Path) -> tuple[str | None, str]:
    """``(player_id, auth)`` with ``auth`` in missing / invalid / ok / registry_error."""
    if token is None:
        return None, "missing"
    try:
        who = registry.verify_token(games_dir, token)
    except registry.RegistryError:
        return None, "registry_error"
    return (who, "ok") if who else (None, "invalid")


def authorize_move(headers: Any, state: Any, games_dir: Path) -> tuple[str | None, str | None]:
    """``(player_id, refusal_code)`` for a move on the loaded ``state``.

    Strict check used by the viewer's move endpoint (#34) : the token's
    player must be the one to move. ``refusal_code`` is ``None`` when the
    move may be played, else one of ``UNAUTHENTICATED``,
    ``REGISTRY_UNREADABLE``, ``NOT_YOUR_TURN``, ``NOT_A_PLAYER``.
    """
    who, auth = _identify(bearer_token(headers), games_dir)
    if auth == "registry_error":
        return None, "REGISTRY_UNREADABLE"
    return who, evaluate("chess_play_move", {}, who, roles_of(state))


def observe(
    tool: str, arguments: dict, headers: Any, games_dir: Path, *, transport: str
) -> dict | None:
    """Record what strict mode would decide for this call ; never raises.

    Returns the journal entry (for tests), or ``None`` when the mode is
    ``off`` or the observation itself failed.
    """
    if auth_mode() != "observe":
        return None
    try:
        who, auth = _identify(bearer_token(headers), games_dir)
        roles = _game_roles(arguments.get("game_id"), games_dir) if tool in RULES else None
        code = "REGISTRY_UNREADABLE" if auth == "registry_error" else None
        code = code or evaluate(tool, arguments, who, roles)
        by = arguments.get("by")
        by_player = _by_player(by, roles)
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "transport": transport,
            "tool": tool,
            "game_id": arguments.get("game_id"),
            "caller": who,
            "auth": auth,
            "by": by if isinstance(by, str) else None,
            "by_matches": None if who is None or not by_player else by_player == who,
            "verdict": "would_refuse" if code else "allow",
            "code": code,
        }
        _append(registry.registry_path(games_dir).parent / OBSERVATIONS_FILENAME, entry)
        return entry
    except Exception as exc:  # noqa: BLE001 — observing must never break a call
        log.warning("auth observation failed for %s : %s", tool, exc)
        return None


def _append(path: Path, entry: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with _atomic.locked(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
