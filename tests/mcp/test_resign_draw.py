"""Tests for `palamede_chessboard.mcp_chessboard_server` — resign + draw offer / agree / decline.

Carved out of the pre-split 1471l test_mcp_chessboard_server.py.
Shared `games_dir` fixture lives in tests/mcp/conftest.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode

# ---- #244 : resign + draw offer/agree/decline ----------------------------


@pytest.mark.asyncio
async def test_resign_ends_game(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "res1", "white": "admin", "black": "Leader"},
    )
    await call_tool("chess_play_move", {"game_id": "res1", "move_san": "e4"})
    body = _decode(await call_tool("chess_resign", {"game_id": "res1", "by": "black"}))
    assert body["status"] == "ok"
    assert body["resign"]["result"] == "1-0"
    assert body["state"]["result"] == "1-0"
    assert body["state"]["is_game_over"] is True
    assert body["state"]["termination_note"] == "Leader resigns"


@pytest.mark.asyncio
async def test_resign_by_player_name(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "resbn", "white": "admin", "black": "Leader"},
    )
    body = _decode(await call_tool("chess_resign", {"game_id": "resbn", "by": "Leader"}))
    assert body["status"] == "ok"
    assert body["resign"]["resigned_by"] == "black"


@pytest.mark.asyncio
async def test_resign_twice_returns_invalid_state(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "res2x", "white": "w", "black": "b"},
    )
    await call_tool("chess_resign", {"game_id": "res2x", "by": "white"})
    body = _decode(await call_tool("chess_resign", {"game_id": "res2x", "by": "black"}))
    assert body["status"] == "error"
    assert body["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_resign_unresolvable_by(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "resbad", "white": "w", "black": "b"},
    )
    body = _decode(await call_tool("chess_resign", {"game_id": "resbad", "by": "phantom"}))
    assert body["status"] == "error"
    assert body["code"] == "CHESSBOARD_ERROR"


@pytest.mark.asyncio
async def test_draw_offer_and_agree(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "dr1", "white": "admin", "black": "Leader"},
    )
    await call_tool("chess_play_move", {"game_id": "dr1", "move_san": "e4"})
    offer = _decode(await call_tool("chess_offer_draw", {"game_id": "dr1", "by": "black"}))
    assert offer["status"] == "ok"
    assert offer["offer"]["pending"] is True
    assert offer["state"]["draw_offer"] == {"by": "black", "ply": 1}

    agree = _decode(await call_tool("chess_agree_draw", {"game_id": "dr1", "by": "white"}))
    assert agree["status"] == "ok"
    assert agree["agree"]["result"] == "1/2-1/2"
    assert agree["state"]["result"] == "1/2-1/2"
    assert agree["state"]["draw_offer"] is None
    assert agree["state"]["is_game_over"] is True


@pytest.mark.asyncio
async def test_draw_offer_idempotent(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "drid", "white": "w", "black": "b"})
    _decode(await call_tool("chess_offer_draw", {"game_id": "drid", "by": "white"}))
    second = _decode(await call_tool("chess_offer_draw", {"game_id": "drid", "by": "white"}))
    assert second["status"] == "ok"
    assert second["offer"]["idempotent"] is True


@pytest.mark.asyncio
async def test_agree_without_offer_invalid(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "drno", "white": "w", "black": "b"})
    body = _decode(await call_tool("chess_agree_draw", {"game_id": "drno", "by": "white"}))
    assert body["status"] == "error"
    assert body["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_agree_own_offer_invalid(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "drself", "white": "w", "black": "b"})
    _decode(await call_tool("chess_offer_draw", {"game_id": "drself", "by": "white"}))
    body = _decode(await call_tool("chess_agree_draw", {"game_id": "drself", "by": "white"}))
    assert body["status"] == "error"
    assert body["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_decline_draw_clears_offer(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "drdec", "white": "w", "black": "b"})
    _decode(await call_tool("chess_offer_draw", {"game_id": "drdec", "by": "white"}))
    body = _decode(await call_tool("chess_decline_draw", {"game_id": "drdec", "by": "black"}))
    assert body["status"] == "ok"
    assert body["decline"]["declined_by"] == "black"
    assert body["state"]["draw_offer"] is None
    assert body["state"]["result"] == "*"


@pytest.mark.asyncio
async def test_resign_rewrites_pgn_with_termination(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "pgnres", "white": "admin", "black": "L"})
    await call_tool("chess_resign", {"game_id": "pgnres", "by": "white"})
    body = _decode(await call_tool("chess_get_raw_pgn", {"game_id": "pgnres"}))
    assert body["status"] == "ok"
    assert '[Result "0-1"]' in body["pgn"]
    assert '[Termination "admin resigns"]' in body["pgn"]
    assert "{admin resigns} 0-1" in body["pgn"]
