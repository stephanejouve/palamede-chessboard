"""Tests for `palamede_chessboard.mcp_chessboard_server` — basic game lifecycle (create / view / play / commentary / raw_pgn / unknown_tool).

Carved out of the pre-split 1471l test_mcp_chessboard_server.py.
Shared `games_dir` fixture lives in tests/mcp/conftest.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode


@pytest.mark.asyncio
async def test_create_view_play_flow(games_dir: Path) -> None:
    created = await call_tool(
        "chess_create_game",
        {
            "game_id": "smoke",
            "white": "Junior",
            "black": "Tephra",
            "arbiter": "Admin",
            "event": "Smoke test",
            "time_control": {"format": "5+3", "start_seconds": 300, "increment_seconds": 3},
        },
    )
    body = _decode(created)
    assert body["status"] == "ok"
    assert body["state"]["metadata"]["arbiter"] == "Admin"
    assert body["state"]["ply"] == 0
    assert body["state"]["turn"] == "white"

    viewed = _decode(await call_tool("chess_view_state", {"game_id": "smoke"}))
    assert viewed["status"] == "ok"
    assert viewed["state"]["ply"] == 0

    played = _decode(
        await call_tool(
            "chess_play_move",
            {"game_id": "smoke", "move_san": "e4", "by": "Junior"},
        )
    )
    assert played["status"] == "ok"
    assert played["move"]["san"] == "e4"
    assert played["state"]["ply"] == 1
    assert played["state"]["turn"] == "black"


@pytest.mark.asyncio
async def test_illegal_move_returns_error(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "illegal", "white": "w", "black": "b"},
    )
    body = _decode(await call_tool("chess_play_move", {"game_id": "illegal", "move_san": "Ke5"}))
    assert body["status"] == "error"
    assert body["code"] == "ILLEGAL_MOVE"


@pytest.mark.asyncio
async def test_play_move_expected_ply_match_succeeds(games_dir: Path) -> None:
    """``expected_ply`` matching current ply = normal path (chess-autoplay #270)."""
    await call_tool("chess_create_game", {"game_id": "exp-ok", "white": "w", "black": "b"})
    body = _decode(
        await call_tool(
            "chess_play_move",
            {"game_id": "exp-ok", "move_san": "e4", "expected_ply": 0},
        )
    )
    assert body["status"] == "ok"
    assert body["state"]["ply"] == 1


@pytest.mark.asyncio
async def test_play_move_stale_ply_returns_error(games_dir: Path) -> None:
    """Concurrent writer advanced the state ; stale caller gets STALE_PLY
    with both ply values exposed, and the second move is NOT applied."""
    await call_tool("chess_create_game", {"game_id": "exp-stale", "white": "w", "black": "b"})
    await call_tool("chess_play_move", {"game_id": "exp-stale", "move_san": "e4"})
    body = _decode(
        await call_tool(
            "chess_play_move",
            {"game_id": "exp-stale", "move_san": "e5", "expected_ply": 0},
        )
    )
    assert body["status"] == "error"
    assert body["code"] == "STALE_PLY"
    assert body["current_ply"] == 1
    assert body["expected_ply"] == 0

    # Board didn't advance : still only e4 played.
    viewed = _decode(await call_tool("chess_view_state", {"game_id": "exp-stale"}))
    assert viewed["state"]["ply"] == 1


@pytest.mark.asyncio
async def test_play_move_expected_ply_omitted_backward_compat(games_dir: Path) -> None:
    """Callers that never set ``expected_ply`` keep the pre-#270 behaviour."""
    await call_tool("chess_create_game", {"game_id": "exp-compat", "white": "w", "black": "b"})
    first = _decode(await call_tool("chess_play_move", {"game_id": "exp-compat", "move_san": "e4"}))
    second = _decode(
        await call_tool("chess_play_move", {"game_id": "exp-compat", "move_san": "e5"})
    )
    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert second["state"]["ply"] == 2


@pytest.mark.asyncio
async def test_create_game_duplicate(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "dup", "white": "w", "black": "b"})
    body = _decode(
        await call_tool("chess_create_game", {"game_id": "dup", "white": "w", "black": "b"})
    )
    assert body["status"] == "error"
    assert body["code"] == "GAME_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_view_state_missing_game(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_view_state", {"game_id": "ghost"}))
    assert body["status"] == "error"
    assert body["code"] == "GAME_NOT_FOUND"


@pytest.mark.asyncio
async def test_commentary_round_trip(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "com", "white": "w", "black": "b"})
    post = _decode(
        await call_tool(
            "chess_post_commentary",
            {"game_id": "com", "ply": 1, "source": "arbiter", "text": "neat"},
        )
    )
    assert post["status"] == "ok"
    listed = _decode(await call_tool("chess_get_commentary", {"game_id": "com"}))
    assert listed["status"] == "ok"
    assert listed["count"] == 1
    assert listed["entries"][0]["text"] == "neat"


@pytest.mark.asyncio
async def test_get_raw_pgn_includes_headers(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {
            "game_id": "pgn",
            "white": "w",
            "black": "b",
            "arbiter": "a",
            "spectators": ["x", "y"],
            "event": "Test",
        },
    )
    body = _decode(await call_tool("chess_get_raw_pgn", {"game_id": "pgn"}))
    assert body["status"] == "ok"
    assert '[White "w"]' in body["pgn"]
    assert '[Arbiter "a"]' in body["pgn"]
    assert '[Spectators "x,y"]' in body["pgn"]


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_nope", {}))
    assert body["status"] == "error"
    assert body["code"] == "UNKNOWN_TOOL"
