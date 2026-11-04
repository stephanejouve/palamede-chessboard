"""``chess_create_game`` refuses a header value that would forge others (#128).

The MCP tool passes ``event``, ``site``, ``round``, the players, the
annotator, the opening, the ECO and ``time_control.format`` as given :
the refusal of ``GameState.save`` reaches the caller as an error, and no
game is created.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode


@pytest.mark.asyncio
async def test_a_forged_time_control_creates_no_game(games_dir: Path) -> None:
    args = {
        "game_id": "forged",
        "white": "stephane",
        "black": "claude-desktop",
        "rated": False,
        "time_control": {"format": '5+3"]\n[Rated "true'},
    }
    body = _decode(await call_tool("chess_create_game", args))
    assert body["status"] == "error"
    assert body["code"] == "CHESSBOARD_ERROR"
    assert "TimeControl" in body["reason"]
    assert not (games_dir / "forged.pgn").exists()


@pytest.mark.asyncio
async def test_a_forged_result_creates_no_game(games_dir: Path) -> None:
    args = {
        "game_id": "forged-result",
        "white": "stephane",
        "black": "claude-desktop",
        "event": 'Amicale"]\n[Result "1-0',
    }
    body = _decode(await call_tool("chess_create_game", args))
    assert body["code"] == "CHESSBOARD_ERROR"
    assert not (games_dir / "forged-result.pgn").exists()
