"""Tests for the viewer realtime router : /api/clock, /api/engine/live, /api/commentary."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import TimeControl
from tests.viewer.conftest import _create_game


class TestClock:
    def test_clock_with_5_plus_3(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir, time_control=TimeControl(format="5+3"))
        # Init the clock just like the MCP server would.
        from palamede_chessboard.chessboard_clock import ClockState, save_clock

        clock = ClockState.init_from_time_control("viewer-test", TimeControl(format="5+3"))
        save_clock(clock, games_dir=games_dir)

        r = client.get("/api/clock?game_id=viewer-test&viewer=spectator")
        body = r.json()
        assert body["clock"] is not None
        assert body["clock"]["white_seconds"] == 300
        assert body["viewer"] == "spectator"

    def test_clock_unrated_returns_null(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir)
        r = client.get("/api/clock?game_id=viewer-test")
        assert r.status_code == 200
        assert r.json()["clock"] is None


class TestEngineLive:
    @pytest.fixture
    def mock_engine(self):
        """Patch chess.engine.SimpleEngine.popen_uci with a deterministic mock."""
        import chess as _chess

        class _Score:
            def is_mate(self):
                return False

            def score(self):
                return 50

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
        # #311 : patcher get_engine_path pour que les tests tournent en CI
        # sans stockfish installé (fail-fast EngineUnavailableError sinon).
        # popen_uci est déjà mocké juste en dessous donc le chemin retourné
        # n'est jamais utilisé réellement.
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
            yield

    def test_arbiter_can_see_live(self, client: TestClient, games_dir: Path, mock_engine) -> None:
        _create_game(games_dir)
        r = client.get("/api/engine/live?game_id=viewer-test&viewer=arbiter&depth=5")
        assert r.status_code == 200
        assert r.json()["analysis"]["score_cp"] == 50

    def test_spectator_can_see_live(self, client: TestClient, games_dir: Path, mock_engine) -> None:
        _create_game(games_dir)
        r = client.get("/api/engine/live?game_id=viewer-test&viewer=spectator&depth=5")
        assert r.status_code == 200

    def test_player_white_blocked_during_live(
        self, client: TestClient, games_dir: Path, mock_engine
    ) -> None:
        _create_game(games_dir, result="*")
        r = client.get("/api/engine/live?game_id=viewer-test&viewer=player_white&depth=5")
        assert r.status_code == 403

    def test_player_white_allowed_post_game(
        self, client: TestClient, games_dir: Path, mock_engine
    ) -> None:
        _create_game(games_dir, result="1-0")
        r = client.get("/api/engine/live?game_id=viewer-test&viewer=player_white&depth=5")
        assert r.status_code == 200
        assert r.json()["is_over"] is True

    def test_unknown_viewer_role_400(
        self, client: TestClient, games_dir: Path, mock_engine
    ) -> None:
        _create_game(games_dir)
        r = client.get("/api/engine/live?game_id=viewer-test&viewer=intruder&depth=5")
        assert r.status_code == 400


class TestEngineHonorSystem:
    """PR #122 : ``viewer`` is documented honor-system, gate = ``is_over``."""

    def test_post_mortem_still_accepted_as_role(
        self, client: TestClient, games_dir: Path, mock_engine=None
    ) -> None:
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

        _create_game(games_dir)
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
            r = client.get("/api/engine/live?game_id=viewer-test&viewer=post_mortem&depth=5")
        assert r.status_code == 200


class TestCommentary:
    def test_post_then_get(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir)
        r = client.post(
            "/api/commentary?game_id=viewer-test",
            json={"ply": 1, "source": "spectator", "text": "ok"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        listed = client.get("/api/commentary?game_id=viewer-test")
        body = listed.json()
        assert body["count"] == 1
        assert body["entries"][0]["text"] == "ok"

    def test_commentary_filter_by_ply(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir)
        client.post(
            "/api/commentary?game_id=viewer-test",
            json={"ply": 3, "source": "a", "text": "3"},
        )
        client.post(
            "/api/commentary?game_id=viewer-test",
            json={"ply": 4, "source": "a", "text": "4"},
        )
        only_3 = client.get("/api/commentary?game_id=viewer-test&ply=3")
        assert only_3.json()["count"] == 1
        assert only_3.json()["entries"][0]["text"] == "3"
