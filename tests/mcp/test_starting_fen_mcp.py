"""``starting_fen`` across MCP calls (issue #36).

Each MCP call reloads the game from disk : an exercise created from a
FEN must keep its position after the first move.
"""

from __future__ import annotations

from pathlib import Path

import chess
import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode

WHITE_FEN = "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"


@pytest.mark.asyncio
async def test_mcp_exercise_end_to_end(games_dir: Path) -> None:
    """Each MCP call reloads from disk : the exercise must hold across calls."""
    created = _decode(
        await call_tool(
            "chess_create_game",
            {
                "game_id": "exo-mcp",
                "white": "stephane",
                "black": "claude-desktop",
                "starting_fen": WHITE_FEN,
            },
        )
    )
    assert created["status"] == "ok"
    played = _decode(await call_tool("chess_play_move", {"game_id": "exo-mcp", "move_san": "Kd2"}))
    assert played["status"] == "ok"
    viewed = _decode(await call_tool("chess_view_state", {"game_id": "exo-mcp"}))
    expected = chess.Board(WHITE_FEN)
    expected.push_san("Kd2")
    assert viewed["state"]["fen"] == expected.fen()
