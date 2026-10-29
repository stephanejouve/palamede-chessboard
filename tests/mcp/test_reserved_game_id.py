"""``chess_create_game`` never creates a ``tablier.*`` game (review Leader #131).

Those ids come only from ``POST /api/games`` with a human's own token : the
Maia bot plays them on that ground. An MCP game named like one would let an
agent ask Maia for a move from the position of its own game.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode


@pytest.mark.parametrize("game_id", ["tablier.stephane.20261029-210000-abcd", "Tablier.stephane.x"])
@pytest.mark.asyncio
async def test_tablier_prefix_is_refused(games_dir: Path, game_id: str) -> None:
    body = _decode(
        await call_tool(
            "chess_create_game",
            {"game_id": game_id, "white": "stephane", "black": "bot-maia-1900", "rated": False},
        )
    )
    assert body["status"] == "error"
    assert "reserved" in body["reason"]
    assert not list(games_dir.glob("*.pgn"))


@pytest.mark.asyncio
async def test_other_ids_still_work(games_dir: Path) -> None:
    body = _decode(
        await call_tool(
            "chess_create_game",
            {
                "game_id": "tablier-like-but-not",
                "white": "stephane",
                "black": "leader",
                "rated": False,
            },
        )
    )
    assert body["status"] == "ok"
