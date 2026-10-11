"""Tests for `palamede_chessboard.mcp_chessboard_server` — engine-guard refusal on in-progress games + audit.

Carved out of the pre-split 1471l test_mcp_chessboard_server.py.
Shared `games_dir` fixture lives in tests/mcp/conftest.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode


class TestEngineGuard:
    """Engine tools refuse to run on in-progress games and audit everything."""

    @pytest.mark.asyncio
    async def test_analyze_position_refused_by_game_id(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        body = _decode(
            await call_tool(
                "chess_analyze_position",
                {"game_id": "live", "depth": 5, "caller": "junior"},
            )
        )
        assert body["status"] == "error"
        assert body["code"] == "ENGINE_FORBIDDEN_INPROGRESS"
        assert body["matched_game_id"] == "live"
        log_path = games_dir / "engine_calls_log.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        assert entry["decision"] == "refused"
        assert entry["tool"] == "chess_analyze_position"
        assert entry["caller"] == "junior"

    @pytest.mark.asyncio
    async def test_analyze_position_refused_by_matching_fen(self, games_dir: Path) -> None:
        """FEN query against the current (post-opening) position of a live game."""
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        # Push 14 plies of Sicilian so the current position is past the
        # opening threshold (OPENING_PLIES_IGNORED = 12).
        for san in [
            "e4",
            "c5",
            "Nf3",
            "d6",
            "d4",
            "cxd4",
            "Nxd4",
            "Nf6",
            "Nc3",
            "a6",
            "Be3",
            "e5",
            "Nb3",
            "Be6",
        ]:
            await call_tool("chess_play_move", {"game_id": "live", "move_san": san})
        viewed = _decode(await call_tool("chess_view_state", {"game_id": "live"}))
        current_fen = viewed["state"]["fen"]
        body = _decode(
            await call_tool(
                "chess_analyze_position",
                {"fen": current_fen, "depth": 5},
            )
        )
        assert body["status"] == "error"
        assert body["code"] == "ENGINE_FORBIDDEN_INPROGRESS"
        assert body["matched_game_id"] == "live"

    @pytest.mark.asyncio
    async def test_analyze_position_refused_by_historical_fen(self, games_dir: Path) -> None:
        """Historical post-opening position : rewinding doesn't escape the guard."""
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        plies = [
            "e4",
            "c5",
            "Nf3",
            "d6",
            "d4",
            "cxd4",
            "Nxd4",
            "Nf6",
            "Nc3",
            "a6",
            "Be3",
            "e5",
            "Nb3",
            "Be6",
            "f3",
            "h6",
        ]
        for san in plies:
            await call_tool("chess_play_move", {"game_id": "live", "move_san": san})
        # Target : position at ply 14 (post-opening) — the live game has
        # advanced to ply 16 so this is a rewound historical position.
        import chess as _chess

        replay = _chess.Board()
        for san in plies[:14]:
            replay.push_san(san)
        body = _decode(
            await call_tool(
                "chess_analyze_position",
                {"fen": replay.fen(), "depth": 5},
            )
        )
        assert body["status"] == "error"
        assert body["code"] == "ENGINE_FORBIDDEN_INPROGRESS"
        assert body["matched_game_id"] == "live"

    @pytest.mark.asyncio
    async def test_analyze_position_allowed_on_opening_fen(self, games_dir: Path) -> None:
        """Opening positions (ply < threshold) stay allowed — shared theory."""
        from unittest.mock import MagicMock, patch

        import chess as _chess

        class _Score:
            def is_mate(self):
                return False

            def score(self):
                return 0

        class _Pov:
            def white(self):
                return _Score()

        engine = MagicMock()
        engine.analyse.return_value = {
            "score": _Pov(),
            "pv": [_chess.Move.from_uci("e2e4")],
            "depth": 5,
        }
        cm = MagicMock()
        cm.__enter__.return_value = engine
        cm.__exit__.return_value = False

        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        for san in ["e4", "c5", "Nf3", "d6"]:
            await call_tool("chess_play_move", {"game_id": "live", "move_san": san})
        # Opening position after 1.e4 — under the threshold, must be allowed.
        replay = _chess.Board()
        replay.push_san("e4")
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
                    {"fen": replay.fen(), "depth": 5},
                )
            )
        assert body["status"] == "ok"

    @pytest.mark.asyncio
    async def test_analyze_position_allowed_on_finished_game(self, games_dir: Path) -> None:
        from unittest.mock import MagicMock, patch

        import chess as _chess

        class _Score:
            def is_mate(self):
                return False

            def score(self):
                return 0

        class _Pov:
            def white(self):
                return _Score()

        engine = MagicMock()
        engine.analyse.return_value = {
            "score": _Pov(),
            "pv": [_chess.Move.from_uci("e2e4")],
            "depth": 5,
        }
        cm = MagicMock()
        cm.__enter__.return_value = engine
        cm.__exit__.return_value = False

        await call_tool(
            "chess_create_game",
            {"game_id": "done", "white": "junior", "black": "leader"},
        )
        await call_tool("chess_resign", {"game_id": "done", "by": "white"})
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
                    {"game_id": "done", "depth": 5},
                )
            )
        assert body["status"] == "ok"
        # The allowed call must still produce an audit entry.
        log_path = games_dir / "engine_calls_log.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        assert entry["decision"] == "allowed"
        assert entry["game_id"] == "done"

    @pytest.mark.asyncio
    async def test_analyze_game_refused_on_in_progress(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        body = _decode(await call_tool("chess_analyze_game", {"game_id": "live", "depth": 5}))
        assert body["status"] == "error"
        assert body["code"] == "ENGINE_FORBIDDEN_INPROGRESS"

    @pytest.mark.asyncio
    async def test_get_my_mistakes_refused_on_in_progress(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        body = _decode(
            await call_tool(
                "chess_get_my_mistakes",
                {"game_id": "live", "player": "white", "depth": 5},
            )
        )
        assert body["status"] == "error"
        assert body["code"] == "ENGINE_FORBIDDEN_INPROGRESS"

    @pytest.mark.asyncio
    async def test_board_facts_still_works_on_in_progress(self, games_dir: Path) -> None:
        """``chess_board_facts`` is explicitly NOT guarded — stays usable."""
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        body = _decode(await call_tool("chess_board_facts", {"game_id": "live"}))
        assert body["status"] == "ok"
        assert "facts" in body
