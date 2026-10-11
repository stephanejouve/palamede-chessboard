"""Tests for `palamede_chessboard.mcp_chessboard_server` — clock creation / start / state / move-advance.

Carved out of the pre-split 1471l test_mcp_chessboard_server.py.
Shared `games_dir` fixture lives in tests/mcp/conftest.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode

# ---- Phase A : clock wiring ----------------------------------------------


@pytest.mark.asyncio
async def test_clock_created_with_5_plus_3(games_dir: Path) -> None:
    body = _decode(
        await call_tool(
            "chess_create_game",
            {
                "game_id": "clk",
                "white": "w",
                "black": "b",
                "time_control": {"format": "5+3"},
            },
        )
    )
    assert body["status"] == "ok"
    assert body["clock"] is not None
    assert body["clock"]["white_seconds"] == 300
    assert body["clock"]["increment_seconds"] == 3
    assert body["clock"]["started"] is False


@pytest.mark.asyncio
async def test_clock_absent_when_unrated(games_dir: Path) -> None:
    body = _decode(
        await call_tool("chess_create_game", {"game_id": "noclk", "white": "w", "black": "b"})
    )
    assert body["status"] == "ok"
    assert body["clock"] is None


@pytest.mark.asyncio
async def test_clock_start_then_state(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {
            "game_id": "started",
            "white": "w",
            "black": "b",
            "time_control": {"format": "5+3"},
        },
    )
    started = _decode(await call_tool("chess_clock_start", {"game_id": "started"}))
    assert started["status"] == "ok"
    assert started["clock"]["active"] == "white"
    assert started["clock"]["started"] is True

    seen = _decode(await call_tool("chess_clock_state", {"game_id": "started"}))
    assert seen["status"] == "ok"
    assert seen["clock"]["active"] == "white"


@pytest.mark.asyncio
async def test_clock_start_no_op_unrated_returns_error(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "unr", "white": "w", "black": "b"})
    body = _decode(await call_tool("chess_clock_start", {"game_id": "unr"}))
    assert body["status"] == "error"
    assert body["code"] == "NO_CLOCK"


@pytest.mark.asyncio
async def test_play_move_advances_clock(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {
            "game_id": "advance",
            "white": "w",
            "black": "b",
            "time_control": {"format": "5+3"},
        },
    )
    await call_tool("chess_clock_start", {"game_id": "advance"})
    body = _decode(await call_tool("chess_play_move", {"game_id": "advance", "move_san": "e4"}))
    assert body["status"] == "ok"
    # Real wall-clock — assert the clock changed *and* turn flipped.
    assert body["clock"]["active"] == "black"
    assert body["clock"]["white_seconds"] != 300


@pytest.mark.asyncio
async def test_clock_state_missing_game_returns_null(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_clock_state", {"game_id": "ghost"}))
    assert body["status"] == "ok"
    assert body["clock"] is None
