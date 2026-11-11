"""Player handlers : preferences, and who the token is (#266)."""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard import refusals
from palamede_chessboard._mcp_shared import REQUEST_HEADERS
from palamede_chessboard.chess_elo import (
    normalize_identity,
)
from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameNotFoundError,
    GameState,
)
from palamede_chessboard.player_prefs import (
    DEFAULT_MAX_CONCURRENT,
    PlayerPrefsError,
    load_prefs,
    max_concurrent_for,
    set_max_concurrent,
)
from palamede_chessboard.players import registry
from palamede_chessboard.players.observe import bearer_token, roles_of


async def _handle_chess_get_player_preferences(arguments: dict, games_dir: Path) -> dict:
    prefs = load_prefs(games_dir)
    player = arguments.get("player")
    if player:
        name = normalize_identity(player)
        return {
            "status": "ok",
            "player": name,
            "max_concurrent": max_concurrent_for(prefs, name),
            "default": DEFAULT_MAX_CONCURRENT,
        }
    return {"status": "ok", "players": prefs, "default": DEFAULT_MAX_CONCURRENT}


async def _handle_chess_set_player_preferences(arguments: dict, games_dir: Path) -> dict:
    try:
        saved = set_max_concurrent(
            games_dir, arguments.get("player"), arguments.get("max_concurrent")
        )
    except PlayerPrefsError as exc:
        raise ChessboardError(str(exc)) from exc
    return {"status": "ok", **saved}


def _token_player(games_dir: Path) -> tuple[str | None, str | None]:
    """``(player, kind)`` of the request's Bearer token, ``(None, None)`` if none
    or unknown : like ``/api/me``, nothing tells a revoked token from a made-up
    one. :class:`registry.RegistryError` when the registry cannot be read."""
    token = bearer_token(REQUEST_HEADERS.get())
    if not token:
        return None, None
    who = registry.verify_token(games_dir, token)
    player = registry.load(games_dir)["players"].get(who) if who else None
    if who is None or player is None:
        return None, None
    return who, player.get("kind")


async def _handle_chess_whoami(arguments: dict, games_dir: Path) -> dict:
    """The MCP twin of ``GET /api/me`` (#266) : who this call's token is.

    Read only. Without a valid token the answer is « anonymous », never an
    error : every client can check its configuration before the strict mode
    of #34 (#268).
    """
    try:
        who, kind = _token_player(games_dir)
    except registry.RegistryError:
        return {"status": "error", "code": "REGISTRY_UNREADABLE"}
    if who is None:
        return {"status": "ok", "authenticated": False, "player": None, "kind": None}
    return {"status": "ok", "authenticated": True, "player": who, "kind": kind}


def _refused(code: str, game_id: str, **extra: str) -> dict:
    """An identity refusal : the code for programs, the viewer's text for the reader (#332)."""
    return {
        "status": "error",
        "code": code,
        "reason": refusals.refusal_text(code),
        "game_id": game_id,
        **extra,
    }


async def _handle_chess_seat(arguments: dict, games_dir: Path) -> dict:
    """The MCP twin of ``GET /api/games/{id}/seat`` (#266) : the token's sides.

    Nothing is played. Refusals as in the viewer : no valid token, or a
    token whose player holds no side in the game. Only the caller's own
    account is named.
    """
    game_id = arguments["game_id"]
    try:
        who, _kind = _token_player(games_dir)
    except registry.RegistryError:
        return _refused("REGISTRY_UNREADABLE", game_id)
    if who is None:
        return _refused("UNAUTHENTICATED", game_id)
    try:
        state = GameState.load(game_id, games_dir=games_dir)
    except GameNotFoundError:
        return {"status": "error", "code": "GAME_NOT_FOUND", "game_id": game_id}
    seats = roles_of(state)["seats"]  # a relay team's members included (#280)
    colors = [color for color in ("white", "black") if normalize_identity(who) in seats[color]]
    if not colors:
        return _refused("NOT_A_PLAYER", game_id, player=who)
    return {"status": "ok", "game_id": game_id, "player": who, "colors": colors}
