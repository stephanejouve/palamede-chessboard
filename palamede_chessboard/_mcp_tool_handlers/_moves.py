"""Move handlers : play, resign, draws, raw PGN, commentary, clock."""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard._mcp_shared import (
    REQUEST_HEADERS,
)
from palamede_chessboard.chessboard_state import (
    GameState,
    get_commentary,
    post_commentary,
)
from palamede_chessboard.clock_moves import (
    NoClockError,
    check_move_allowed,
    clock_after_move,
    game_lock,
    settle_flag,
    start_clock,
    stop_clock_locked,
)


async def _handle_chess_play_move(arguments: dict, games_dir: Path) -> dict:
    with game_lock(arguments["game_id"], games_dir):
        state = GameState.load(arguments["game_id"], games_dir=games_dir)
        # The mover is whoever was *to move* before play_move pushed
        # the SAN — capture it before the board flips.
        mover = "white" if state.board.turn else "black"
        check_move_allowed(state, games_dir)  # no move before the clock (FIDE 6.6, #330)
        move_result = state.play_move(
            arguments["move_san"],
            by=arguments.get("by"),
            expected_ply=arguments.get("expected_ply"),
        )
        clock = clock_after_move(state, mover, games_dir)
    return {
        "status": "ok",
        "move": move_result,
        "state": state.to_state_dict(),
        "clock": clock.to_dict() if clock is not None else None,
    }


async def _handle_chess_resign(arguments: dict, games_dir: Path) -> dict:
    # One hold for the end and the clock : a flag falling meanwhile cannot
    # cross the resignation (#289).
    with game_lock(arguments["game_id"], games_dir):
        state = GameState.load(arguments["game_id"], games_dir=games_dir)
        result = state.resign(by=arguments["by"])
        stop_clock_locked(arguments["game_id"], games_dir)  # no move ends it (#287)
    return {"status": "ok", "resign": result, "state": state.to_state_dict()}


async def _handle_chess_offer_draw(arguments: dict, games_dir: Path) -> dict:
    state = GameState.load(arguments["game_id"], games_dir=games_dir)
    result = state.offer_draw(by=arguments["by"])
    return {"status": "ok", "offer": result, "state": state.to_state_dict()}


async def _handle_chess_agree_draw(arguments: dict, games_dir: Path) -> dict:
    with game_lock(arguments["game_id"], games_dir):
        state = GameState.load(arguments["game_id"], games_dir=games_dir)
        result = state.agree_draw(by=arguments["by"])
        stop_clock_locked(arguments["game_id"], games_dir)  # an agreed draw ends it too (#287)
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
    clock = settle_flag(arguments["game_id"], games_dir, REQUEST_HEADERS.get())
    return {"status": "ok", "clock": clock.to_dict() if clock is not None else None}


async def _handle_chess_clock_start(arguments: dict, games_dir: Path) -> dict:
    # Under the game lock, against the side to move, White before the first move ;
    # any participant ; tablier.* too ; started already : unchanged (#330).
    try:
        clock = start_clock(arguments["game_id"], games_dir)
    except NoClockError:
        return {
            "status": "error",
            "code": "NO_CLOCK",
            "reason": "Game has no clock (TimeControl=unrated).",
        }
    return {"status": "ok", "clock": clock.to_dict()}
