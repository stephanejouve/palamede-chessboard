"""Tests for `palamede_chessboard.mcp_chessboard_server` — chess_list_games tool.

Carved out of the pre-split 1471l test_mcp_chessboard_server.py.
Shared `games_dir` fixture lives in tests/mcp/conftest.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode


class TestListGamesTool:
    @pytest.mark.asyncio
    async def test_empty_dir(self, games_dir: Path) -> None:
        # Remove the arbiter pidfile side-effect — no game created.
        body = _decode(await call_tool("chess_list_games", {}))
        assert body["status"] == "ok"
        assert body["count"] == 0
        assert body["games"] == []

    @pytest.mark.asyncio
    async def test_returns_summaries(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "g1", "white": "junior", "black": "leader"},
        )
        await call_tool(
            "chess_create_game",
            {"game_id": "g2", "white": "admin", "black": "leader"},
        )
        body = _decode(await call_tool("chess_list_games", {}))
        assert body["status"] == "ok"
        assert body["count"] == 2
        ids = {g["game_id"] for g in body["games"]}
        assert ids == {"g1", "g2"}
        for g in body["games"]:
            assert set(g.keys()) == {
                "game_id",
                "white",
                "black",
                "result",
                "ply",
                "turn",
                "is_game_over",
                "mtime",
            }
            assert g["turn"] == "white"

    @pytest.mark.asyncio
    async def test_player_filter(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "jl", "white": "junior", "black": "leader"},
        )
        await call_tool(
            "chess_create_game",
            {"game_id": "ac", "white": "admin", "black": "cd"},
        )
        body = _decode(await call_tool("chess_list_games", {"player": "junior"}))
        assert body["count"] == 1
        assert body["games"][0]["game_id"] == "jl"

    @pytest.mark.asyncio
    async def test_active_only(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "a", "black": "b"},
        )
        await call_tool(
            "chess_create_game",
            {"game_id": "done", "white": "a", "black": "b"},
        )
        await call_tool("chess_resign", {"game_id": "done", "by": "white"})
        body = _decode(await call_tool("chess_list_games", {"active_only": True}))
        assert body["count"] == 1
        assert body["games"][0]["game_id"] == "live"
