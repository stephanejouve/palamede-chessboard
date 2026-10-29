"""Per-tool handlers for the MCP chessboard server.

Each handler takes ``(arguments: dict, games_dir: Path)`` and returns a
plain ``dict`` payload (not wrapped in TextContent). The slim transport
module wraps the result via ``_mcp_response`` and catches domain
exceptions. Keeping handlers return-dict (not list[TextContent]) keeps
each one under the 80-line budget while preserving a single
exception-catching layer in ``call_tool``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import chess

from palamede_chessboard._mcp_shared import (
    _compute_board_facts,
    _compute_move_facts,
    _journal_board_facts_gap,
    _refuse_reserved_game_id,
    _require_arbiter_or_daemon,
)
from palamede_chessboard.chess_elo import (
    PROVISIONAL_GAMES,
    load_cached_ratings,
    normalize_identity,
    rebuild_and_persist_cache,
)
from palamede_chessboard.chess_engine_guard import (
    check_engine_call,
    log_engine_call,
    mask_walk_entries,
)
from palamede_chessboard.chessboard_clock import (
    init_and_save_clock,
    load_clock,
    result_from_flag_fall,
    save_clock,
)
from palamede_chessboard.chessboard_engine import (
    DEFAULT_DEPTH,
    analyze_game,
    analyze_position,
    get_my_mistakes,
)
from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameMetadata,
    GameState,
    TimeControl,
    get_commentary,
    list_games,
    post_commentary,
)
from palamede_chessboard.player_prefs import (
    DEFAULT_MAX_CONCURRENT,
    PlayerPrefsError,
    load_prefs,
    max_concurrent_for,
    set_max_concurrent,
)


async def _handle_chess_create_game(arguments: dict, games_dir: Path) -> dict:
    _require_arbiter_or_daemon(arguments.get("arbiter"), games_dir)
    _refuse_reserved_game_id(arguments.get("game_id"))
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


async def _handle_chess_play_move(arguments: dict, games_dir: Path) -> dict:
    state = GameState.load(arguments["game_id"], games_dir=games_dir)
    # The mover is whoever was *to move* before play_move pushed
    # the SAN — capture it before the board flips.
    mover = "white" if state.board.turn else "black"
    move_result = state.play_move(
        arguments["move_san"],
        by=arguments.get("by"),
        expected_ply=arguments.get("expected_ply"),
    )
    clock = load_clock(arguments["game_id"], games_dir=games_dir)
    if clock is not None and clock.started:
        clock.on_move_played(mover=mover)
        if clock.flag_fall is not None:
            state.metadata.result = result_from_flag_fall(clock.flag_fall)
            state.save()
        save_clock(clock, games_dir=games_dir)
    return {
        "status": "ok",
        "move": move_result,
        "state": state.to_state_dict(),
        "clock": clock.to_dict() if clock is not None else None,
    }


async def _handle_chess_resign(arguments: dict, games_dir: Path) -> dict:
    state = GameState.load(arguments["game_id"], games_dir=games_dir)
    result = state.resign(by=arguments["by"])
    return {"status": "ok", "resign": result, "state": state.to_state_dict()}


async def _handle_chess_offer_draw(arguments: dict, games_dir: Path) -> dict:
    state = GameState.load(arguments["game_id"], games_dir=games_dir)
    result = state.offer_draw(by=arguments["by"])
    return {"status": "ok", "offer": result, "state": state.to_state_dict()}


async def _handle_chess_agree_draw(arguments: dict, games_dir: Path) -> dict:
    state = GameState.load(arguments["game_id"], games_dir=games_dir)
    result = state.agree_draw(by=arguments["by"])
    return {"status": "ok", "agree": result, "state": state.to_state_dict()}


async def _handle_chess_decline_draw(arguments: dict, games_dir: Path) -> dict:
    state = GameState.load(arguments["game_id"], games_dir=games_dir)
    result = state.decline_draw(by=arguments["by"])
    return {"status": "ok", "decline": result, "state": state.to_state_dict()}


async def _handle_chess_get_raw_pgn(arguments: dict, games_dir: Path) -> dict:
    state = GameState.load(arguments["game_id"], games_dir=games_dir)
    return {"status": "ok", "pgn": state.raw_pgn()}


async def _handle_chess_post_commentary(arguments: dict, games_dir: Path) -> dict:
    entry = post_commentary(
        arguments["game_id"],
        ply=arguments["ply"],
        source=arguments["source"],
        text=arguments["text"],
        games_dir=games_dir,
    )
    return {"status": "ok", "entry": entry}


async def _handle_chess_get_commentary(arguments: dict, games_dir: Path) -> dict:
    entries = get_commentary(
        arguments["game_id"],
        ply=arguments.get("ply"),
        games_dir=games_dir,
    )
    return {"status": "ok", "count": len(entries), "entries": entries}


async def _handle_chess_clock_state(arguments: dict, games_dir: Path) -> dict:
    clock = load_clock(arguments["game_id"], games_dir=games_dir)
    return {"status": "ok", "clock": clock.to_dict() if clock is not None else None}


async def _handle_chess_clock_start(arguments: dict, games_dir: Path) -> dict:
    clock = load_clock(arguments["game_id"], games_dir=games_dir)
    if clock is None:
        return {
            "status": "error",
            "code": "NO_CLOCK",
            "reason": "Game has no clock (TimeControl=unrated).",
        }
    clock.start()
    save_clock(clock, games_dir=games_dir)
    return {"status": "ok", "clock": clock.to_dict()}


def _resolve_board_facts_input(
    arguments: dict, games_dir: Path
) -> tuple[chess.Board | None, str | None, dict | None]:
    """Return (board, game_id, error_payload). Exactly one of board/error is set."""
    fen = arguments.get("fen")
    game_id = arguments.get("game_id")
    if fen and game_id:
        return (
            None,
            None,
            {
                "status": "error",
                "code": "INVALID_ARG",
                "reason": "chess_board_facts : ``game_id`` et ``fen`` sont exclusifs, fournir un seul.",
            },
        )
    if fen:
        try:
            return chess.Board(fen), None, None
        except ValueError as exc:
            return (
                None,
                None,
                {
                    "status": "error",
                    "code": "INVALID_ARG",
                    "reason": f"chess_board_facts : FEN invalide ({exc}).",
                },
            )
    if game_id:
        state = GameState.load(game_id, games_dir=games_dir)
        return state.board, game_id, None
    return (
        None,
        None,
        {
            "status": "error",
            "code": "INVALID_ARG",
            "reason": "chess_board_facts : fournir ``game_id`` ou ``fen``.",
        },
    )


async def _handle_chess_board_facts(arguments: dict, games_dir: Path) -> dict:
    board, game_id, err = _resolve_board_facts_input(arguments, games_dir)
    if err is not None:
        return err
    assert board is not None  # narrows for mypy
    facts = _compute_board_facts(board)
    payload: dict[str, Any] = {"status": "ok", "facts": facts}
    expected = arguments.get("expected")
    if expected:
        payload["expected"] = expected
        if game_id:
            _journal_board_facts_gap(game_id, board, expected, facts)
    move_san = arguments.get("move_san")
    if move_san:
        payload["move_facts"] = _compute_move_facts(board, move_san)
    return payload


def _resolve_analyze_fen(
    game_id: str | None, fen: str | None, ply_arg: Any, games_dir: Path
) -> str:
    """Build the FEN to feed the engine from the (game_id, fen, ply) triple."""
    if fen:
        return fen
    assert game_id is not None
    state = GameState.load(game_id, games_dir=games_dir)
    if ply_arg is None:
        return state.board.fen()
    replay = state.board.root()
    for san in state.san_history[: int(ply_arg)]:
        replay.push(replay.parse_san(san))
    return replay.fen()


async def _handle_chess_analyze_position(arguments: dict, games_dir: Path) -> dict:
    depth = int(arguments.get("depth") or DEFAULT_DEPTH)
    fen = arguments.get("fen")
    game_id = arguments.get("game_id")
    if not fen and not game_id:
        return {
            "status": "error",
            "code": "MISSING_INPUT",
            "reason": "chess_analyze_position needs either fen or game_id.",
        }
    ply_arg = arguments.get("ply")
    decision = check_engine_call(
        games_dir,
        tool="chess_analyze_position",
        game_id=game_id,
        fen=fen,
        ply=int(ply_arg) if ply_arg is not None else None,
    )
    log_engine_call(
        tool="chess_analyze_position",
        decision=decision,
        game_id=game_id,
        fen=fen,
        caller=arguments.get("caller"),
    )
    if not decision["allowed"]:
        return {
            "status": "error",
            "code": "ENGINE_FORBIDDEN_INPROGRESS",
            "reason": decision["reason"],
            "matched_game_id": decision["matched_game_id"],
        }
    resolved_fen = _resolve_analyze_fen(game_id, fen, ply_arg, games_dir)
    analysis = analyze_position(resolved_fen, depth=depth)
    return {"status": "ok", "analysis": analysis.to_dict()}


async def _handle_chess_analyze_game(arguments: dict, games_dir: Path) -> dict:
    depth = int(arguments.get("depth") or DEFAULT_DEPTH)
    game_id = arguments["game_id"]
    decision = check_engine_call(games_dir, tool="chess_analyze_game", game_id=game_id)
    if not decision["allowed"]:
        log_engine_call(
            tool="chess_analyze_game",
            decision=decision,
            game_id=game_id,
            caller=arguments.get("caller"),
        )
        return {
            "status": "error",
            "code": "ENGINE_FORBIDDEN_INPROGRESS",
            "reason": decision["reason"],
            "matched_game_id": decision["matched_game_id"],
        }
    state = GameState.load(game_id, games_dir=games_dir)
    walk = analyze_game(state, depth=depth)
    walk, mask_count, mask_details = mask_walk_entries(walk, games_dir)
    log_engine_call(
        tool="chess_analyze_game",
        decision=decision,
        game_id=game_id,
        caller=arguments.get("caller"),
        extra={"masked_plies": mask_details} if mask_details else None,
    )
    return {
        "status": "ok",
        "count": len(walk),
        "walk": [e.to_dict() for e in walk],
        "masked_count": mask_count,
        "masked_plies": mask_details,
    }


async def _handle_chess_get_my_mistakes(arguments: dict, games_dir: Path) -> dict:
    depth = int(arguments.get("depth") or DEFAULT_DEPTH)
    min_classification = arguments.get("min_classification", "inaccuracy")
    game_id = arguments["game_id"]
    decision = check_engine_call(games_dir, tool="chess_get_my_mistakes", game_id=game_id)
    if not decision["allowed"]:
        log_engine_call(
            tool="chess_get_my_mistakes",
            decision=decision,
            game_id=game_id,
            caller=arguments.get("caller"),
        )
        return {
            "status": "error",
            "code": "ENGINE_FORBIDDEN_INPROGRESS",
            "reason": decision["reason"],
            "matched_game_id": decision["matched_game_id"],
        }
    state = GameState.load(game_id, games_dir=games_dir)
    walk = analyze_game(state, depth=depth)
    walk, mask_count, mask_details = mask_walk_entries(walk, games_dir)
    log_engine_call(
        tool="chess_get_my_mistakes",
        decision=decision,
        game_id=game_id,
        caller=arguments.get("caller"),
        extra={"masked_plies": mask_details} if mask_details else None,
    )
    mistakes = get_my_mistakes(
        walk, mover=arguments["player"], min_classification=min_classification
    )
    return {
        "status": "ok",
        "count": len(mistakes),
        "mistakes": [e.to_dict() for e in mistakes],
        "masked_count": mask_count,
        "masked_plies": mask_details,
    }


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


TOOL_HANDLERS: dict[str, Callable[[dict, Path], Awaitable[dict]]] = {
    "chess_create_game": _handle_chess_create_game,
    "chess_view_state": _handle_chess_view_state,
    "chess_list_games": _handle_chess_list_games,
    "chess_get_elo": _handle_chess_get_elo,
    "chess_play_move": _handle_chess_play_move,
    "chess_resign": _handle_chess_resign,
    "chess_offer_draw": _handle_chess_offer_draw,
    "chess_agree_draw": _handle_chess_agree_draw,
    "chess_decline_draw": _handle_chess_decline_draw,
    "chess_get_raw_pgn": _handle_chess_get_raw_pgn,
    "chess_post_commentary": _handle_chess_post_commentary,
    "chess_get_commentary": _handle_chess_get_commentary,
    "chess_clock_state": _handle_chess_clock_state,
    "chess_clock_start": _handle_chess_clock_start,
    "chess_board_facts": _handle_chess_board_facts,
    "chess_analyze_position": _handle_chess_analyze_position,
    "chess_analyze_game": _handle_chess_analyze_game,
    "chess_get_my_mistakes": _handle_chess_get_my_mistakes,
    "chess_get_player_preferences": _handle_chess_get_player_preferences,
    "chess_set_player_preferences": _handle_chess_set_player_preferences,
}
