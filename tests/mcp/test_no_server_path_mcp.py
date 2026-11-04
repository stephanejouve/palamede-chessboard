"""The MCP server's error replies carry no path of the server (#107)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from palamede_chessboard import mcp_chessboard_server
from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.player_prefs import prefs_path
from tests.mcp.conftest import _decode


@pytest.mark.asyncio
async def test_game_not_found_names_the_game(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_view_state", {"game_id": "ghost"}))
    assert body == {"status": "error", "code": "GAME_NOT_FOUND", "reason": "game not found: ghost"}


@pytest.mark.asyncio
async def test_game_already_exists_names_the_game(games_dir: Path) -> None:
    args = {"game_id": "twice", "white": "w", "black": "b"}
    await call_tool("chess_create_game", args)
    body = _decode(await call_tool("chess_create_game", args))
    assert body["code"] == "GAME_ALREADY_EXISTS"
    assert body["reason"] == "game already exists: twice"


@pytest.mark.asyncio
async def test_unreadable_preferences_say_no_path(games_dir: Path) -> None:
    prefs_path(games_dir).write_text("{not json", encoding="utf-8")
    args = {"player": "stephane", "max_concurrent": 2}
    body = _decode(await call_tool("chess_set_player_preferences", args))
    assert body["code"] == "CHESSBOARD_ERROR"
    assert str(games_dir) not in body["reason"]


@pytest.mark.asyncio
async def test_an_internal_error_is_said_without_its_text(
    games_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(arguments: dict, gdir: Path) -> dict:
        raise OSError(f"[Errno 13] Permission denied: '{gdir}/registry.json'")

    monkeypatch.setitem(mcp_chessboard_server.TOOL_HANDLERS, "chess_view_state", broken)
    with caplog.at_level(logging.ERROR):
        body = _decode(await call_tool("chess_view_state", {"game_id": "any"}))
    assert body == {"status": "error", "code": "INTERNAL", "reason": "internal error"}
    assert str(games_dir) in caplog.text
