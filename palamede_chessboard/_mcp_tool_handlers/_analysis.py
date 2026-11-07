"""Analysis handlers : board facts, engine analysis, mistakes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chess

from palamede_chessboard._mcp_shared import (
    _compute_board_facts,
    _compute_move_facts,
    _journal_board_facts_gap,
)
from palamede_chessboard.chess_engine_guard import (
    check_engine_call,
    log_engine_call,
    mask_walk_entries,
)
from palamede_chessboard.chessboard_engine import (
    DEFAULT_DEPTH,
    analyze_game,
    analyze_position,
    get_my_mistakes,
)
from palamede_chessboard.chessboard_state import (
    GameState,
)


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
