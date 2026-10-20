"""MCP tools ``chess_get_player_preferences`` / ``chess_set_player_preferences`` (#38)."""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.player_prefs import PREFS_ENV
from tests.mcp.conftest import _decode


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PREFS_ENV, raising=False)


@pytest.mark.asyncio
async def test_get_default_then_set_then_get(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_get_player_preferences", {"player": "leader"}))
    assert body == {"status": "ok", "player": "leader", "max_concurrent": 1, "default": 1}

    saved = _decode(
        await call_tool("chess_set_player_preferences", {"player": "Leader", "max_concurrent": 3})
    )
    assert saved == {"status": "ok", "player": "leader", "max_concurrent": 3}

    body = _decode(await call_tool("chess_get_player_preferences", {"player": "leader"}))
    assert body["max_concurrent"] == 3
    listing = _decode(await call_tool("chess_get_player_preferences", {}))
    assert listing["players"] == {"leader": {"max_concurrent": 3}}


@pytest.mark.parametrize("bad", [0, 11, "2", True])
@pytest.mark.asyncio
async def test_set_refuses_invalid(games_dir: Path, bad) -> None:
    body = _decode(
        await call_tool("chess_set_player_preferences", {"player": "leader", "max_concurrent": bad})
    )
    assert body["status"] == "error"
    assert body["code"] == "CHESSBOARD_ERROR"
    assert "max_concurrent" in body["reason"]
