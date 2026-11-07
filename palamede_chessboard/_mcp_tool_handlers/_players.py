"""Player handlers : preferences."""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.chess_elo import (
    normalize_identity,
)
from palamede_chessboard.chessboard_state import (
    ChessboardError,
)
from palamede_chessboard.player_prefs import (
    DEFAULT_MAX_CONCURRENT,
    PlayerPrefsError,
    load_prefs,
    max_concurrent_for,
    set_max_concurrent,
)


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
