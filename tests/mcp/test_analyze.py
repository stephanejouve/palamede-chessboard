"""Tests for `palamede_chessboard.mcp_chessboard_server` — chess_analyze_position + chess_analyze_game (mocked engine).

Carved out of the pre-split 1471l test_mcp_chessboard_server.py.
Shared `games_dir` fixture lives in tests/mcp/conftest.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode

# ---- Phase B : engine tools ----------------------------------------------


@pytest.mark.asyncio
async def test_analyze_position_via_fen(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock, patch

    class _Score:
        def is_mate(self):
            return False

        def score(self):
            return 33

    class _Pov:
        def white(self):
            return _Score()

    import chess as _chess

    engine = MagicMock()
    engine.analyse.return_value = {
        "score": _Pov(),
        "pv": [_chess.Move.from_uci("e2e4")],
        "depth": 5,
    }
    cm = MagicMock()
    cm.__enter__.return_value = engine
    cm.__exit__.return_value = False

    # #311 : patcher get_engine_path pour tourner en CI sans stockfish.
    with (
        patch(
            "palamede_chessboard.chessboard_engine.get_engine_path",
            return_value="/bin/true",
        ),
        patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=cm,
        ),
    ):
        body = _decode(
            await call_tool(
                "chess_analyze_position",
                {
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "depth": 5,
                },
            )
        )
    assert body["status"] == "ok"
    assert body["analysis"]["score_cp"] == 33
    assert body["analysis"]["best_move_uci"] == "e2e4"


@pytest.mark.asyncio
async def test_analyze_position_missing_inputs(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_analyze_position", {}))
    assert body["status"] == "error"
    assert body["code"] == "MISSING_INPUT"


@pytest.mark.asyncio
async def test_analyze_game_returns_walk(games_dir: Path) -> None:
    from unittest.mock import MagicMock, patch

    class _Score:
        def __init__(self, cp):
            self._cp = cp

        def is_mate(self):
            return False

        def score(self):
            return self._cp

    class _Pov:
        def __init__(self, cp):
            self._cp = cp

        def white(self):
            return _Score(self._cp)

    import chess as _chess

    # create game with 1 move, then resign — chess_analyze_game is now
    # gated behind the engine guard and refuses in-progress games.
    await call_tool("chess_create_game", {"game_id": "ana-game", "white": "w", "black": "b"})
    await call_tool("chess_play_move", {"game_id": "ana-game", "move_san": "e4"})
    await call_tool("chess_resign", {"game_id": "ana-game", "by": "white"})

    engine = MagicMock()
    engine.analyse.side_effect = [
        {
            "score": _Pov(20),
            "pv": [_chess.Move.from_uci("e2e4")],
            "depth": 5,
        },
        {
            "score": _Pov(25),
            "pv": [_chess.Move.from_uci("e7e5")],
            "depth": 5,
        },
    ]
    cm = MagicMock()
    cm.__enter__.return_value = engine
    cm.__exit__.return_value = False

    # #311 : patcher get_engine_path pour tourner en CI sans stockfish.
    with (
        patch(
            "palamede_chessboard.chessboard_engine.get_engine_path",
            return_value="/bin/true",
        ),
        patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=cm,
        ),
    ):
        body = _decode(await call_tool("chess_analyze_game", {"game_id": "ana-game", "depth": 5}))
    assert body["status"] == "ok"
    assert body["count"] == 1
    assert body["walk"][0]["mover"] == "white"
    assert body["walk"][0]["san"] == "e4"
    assert body["walk"][0]["classification"] in {
        "good",
        "inaccuracy",
        "mistake",
        "blunder",
    }


@pytest.mark.asyncio
async def test_engine_unavailable_surfaces_error(
    games_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_CHESSBOARD_STOCKFISH", "/definitely/not/here")
    body = _decode(
        await call_tool(
            "chess_analyze_position",
            {
                "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            },
        )
    )
    assert body["status"] == "error"
    assert body["code"] == "ENGINE_UNAVAILABLE"
