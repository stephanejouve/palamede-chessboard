"""Game handlers : create, view, list, Elo."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from palamede_chessboard._mcp_shared import (
    _refuse_reserved_game_id,
    _refuse_unaccountable_players,
    _relay_teams,
    _require_arbiter_or_daemon,
)
from palamede_chessboard.chess_elo import (
    PROVISIONAL_GAMES,
    load_cached_ratings,
    normalize_identity,
    rebuild_and_persist_cache,
)
from palamede_chessboard.chessboard_clock import (
    init_and_save_clock,
)
from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameMetadata,
    GameState,
    TimeControl,
    list_games,
)
from palamede_chessboard.cours import create_lesson


async def _handle_chess_create_game(arguments: dict, games_dir: Path) -> dict:
    _require_arbiter_or_daemon(arguments.get("arbiter"), games_dir)
    _refuse_reserved_game_id(arguments.get("game_id"))
    _refuse_unaccountable_players(arguments.get("white"), arguments.get("black"))
    white_team, black_team = _relay_teams(arguments, games_dir)
    # Issue #33 : strict boolean, so a malformed client (``"false"``,
    # ``0``) can never create a rated game by accident.
    rated = arguments.get("rated", True)
    if not isinstance(rated, bool):
        raise ChessboardError(f"rated must be a JSON boolean (true/false), got {rated!r}")
    tc_raw = arguments.get("time_control") or {}
    tc = TimeControl(
        format=tc_raw.get("format", "unrated"),
        start_seconds=tc_raw.get("start_seconds"),
        increment_seconds=tc_raw.get("increment_seconds"),
    )
    metadata = GameMetadata(
        game_id=arguments["game_id"],
        event=arguments.get("event", ""),
        site=arguments.get("site", ""),
        round=arguments.get("round", "1"),
        white=arguments["white"],
        black=arguments["black"],
        arbiter=arguments.get("arbiter") or None,
        spectators=list(arguments.get("spectators") or []),
        white_team=white_team,
        black_team=black_team,
        time_control=tc,
        annotator=arguments.get("annotator") or None,
        opening=arguments.get("opening") or None,
        eco=arguments.get("eco") or None,
        rated=rated,
    )
    state = GameState.create(
        metadata,
        games_dir=games_dir,
        starting_fen=arguments.get("starting_fen") or None,
    )
    clock = init_and_save_clock(state.metadata.game_id, tc, games_dir=games_dir)
    return {
        "status": "ok",
        "state": state.to_state_dict(),
        "clock": clock.to_dict() if clock is not None else None,
    }


async def _handle_chess_create_lesson(arguments: dict, games_dir: Path) -> dict:
    """:func:`create_lesson` ; MCP adds only its arbiter gate, like ``chess_create_game``."""
    _require_arbiter_or_daemon(arguments.get("arbiter"), games_dir)
    state = create_lesson(
        games_dir,
        theme=arguments.get("theme"),
        white=arguments.get("white"),
        black=arguments.get("black"),
        arbiter=arguments.get("arbiter"),
    )
    return {"status": "ok", "game_id": state.metadata.game_id, "state": state.to_state_dict()}


async def _handle_chess_view_state(arguments: dict, games_dir: Path) -> dict:
    state = GameState.load(arguments["game_id"], games_dir=games_dir)
    return {"status": "ok", "state": state.to_state_dict()}


async def _handle_chess_list_games(arguments: dict, games_dir: Path) -> dict:
    games = list_games(
        games_dir,
        player=arguments.get("player"),
        active_only=bool(arguments.get("active_only", False)),
    )
    return {"status": "ok", "count": len(games), "games": games}


def _collect_elo_players(board: Any, player_filter: str | None) -> list[dict[str, Any]]:
    players_payload: list[dict[str, Any]] = []
    if board is None:
        return players_payload
    items = sorted(board.players.items(), key=lambda kv: (-kv[1].rating, kv[0]))
    for name_, rating in items:
        if player_filter and name_ != player_filter:
            continue
        players_payload.append(
            {
                "name": name_,
                "rating": rating.rating,
                "games": rating.games,
                "provisional": rating.games < PROVISIONAL_GAMES,
            }
        )
    return players_payload


async def _handle_chess_get_elo(arguments: dict, games_dir: Path) -> dict:
    raw_player = arguments.get("player")
    player_filter = normalize_identity(raw_player) if raw_player else None
    include_history = bool(arguments.get("include_history", False))
    cache_only = bool(arguments.get("cache_only", False))

    if cache_only:
        board = load_cached_ratings(games_dir=games_dir)
        cache_hit = board is not None
    else:
        board, _ = rebuild_and_persist_cache(games_dir=games_dir)
        cache_hit = False

    players_payload = _collect_elo_players(board, player_filter)
    payload: dict[str, Any] = {
        "status": "ok",
        "cache_hit": cache_hit,
        "provisional_games": PROVISIONAL_GAMES,
        "count": len(players_payload),
        "players": players_payload,
    }
    if include_history and board is not None:
        payload["history"] = [u.to_dict() for u in board.history]
    return payload
