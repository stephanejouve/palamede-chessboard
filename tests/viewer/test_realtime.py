"""Tests for the viewer realtime router : /api/clock, /api/engine/live, /api/commentary."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import chess
import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.viewer.models import VIEWER_ROLES
from palamede_chessboard.viewer.routers.realtime import ENGINE_REFUSED
from tests.viewer.conftest import _create_game


def _fen(*moves: str) -> str:
    board = chess.Board()
    for san in moves:
        board.push_san(san)
    return board.fen()


#: 1.e4 e5 : a game set up from it keeps all its positions (#204).
AFTER_E4_E5 = _fen("e4", "e5")


def _live_from(games_dir: Path, game_id: str, fen: str) -> GameState:
    meta = GameMetadata(
        game_id=game_id, white="w", black="b", time_control=TimeControl(format="unrated")
    )
    return GameState.create(meta, games_dir=games_dir, starting_fen=fen)


def _log(engine_log: Path) -> list[dict]:
    if not engine_log.exists():
        return []
    return [json.loads(line) for line in engine_log.read_text(encoding="utf-8").splitlines()]


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
        assert body["clock"]["white_seconds"] == 303  # 300 + 3 (#160)
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

    def test_a_live_game_is_refused_whatever_the_role(
        self, client: TestClient, games_dir: Path, mock_engine, engine_log: Path
    ) -> None:
        # #206 : ``viewer`` is declared by the client, it opens nothing.
        _create_game(games_dir, result="*")
        for role in VIEWER_ROLES:
            r = client.get(f"/api/engine/live?game_id=viewer-test&viewer={role}&depth=5")
            assert r.status_code == 403, role
            assert r.json()["detail"] == ENGINE_REFUSED
        lines = _log(engine_log)
        assert [line["viewer"] for line in lines] == list(VIEWER_ROLES)
        assert {(line["decision"], line["caller"], line["tool"]) for line in lines} == {
            ("refused", "viewer", "chess_analyze_position")
        }

    def test_a_free_fen_of_a_live_game_is_refused(
        self, client: TestClient, games_dir: Path, mock_engine, engine_log: Path
    ) -> None:
        # Cite a finished game, ask for the position of a live one.
        _create_game(games_dir, game_id="done", result="1-0")
        _live_from(games_dir, "live", AFTER_E4_E5)
        for fen in (AFTER_E4_E5, chess.Board(AFTER_E4_E5).mirror().fen()):
            r = client.get(
                "/api/engine/live?game_id=done&viewer=spectator&depth=5&fen=" + quote(fen)
            )
            assert r.status_code == 403
        assert [line["matched_game_id"] for line in _log(engine_log)] == ["live", "live"]

    def test_an_open_exercise_is_refused(
        self, client: TestClient, games_dir: Path, mock_engine
    ) -> None:
        _live_from(games_dir, "exo.human-player.20261104-210000-abcd", AFTER_E4_E5)
        r = client.get(
            "/api/engine/live?game_id=exo.human-player.20261104-210000-abcd"
            "&viewer=spectator&depth=5"
        )
        assert r.status_code == 403

    def test_a_finished_game_is_evaluated_and_logged(
        self, client: TestClient, games_dir: Path, mock_engine, engine_log: Path
    ) -> None:
        _create_game(games_dir, result="1-0")
        r = client.get("/api/engine/live?game_id=viewer-test&viewer=player_white&depth=5")
        assert r.status_code == 200
        assert r.json()["is_over"] is True
        assert r.json()["analysis"]["score_cp"] == 50
        [line] = _log(engine_log)
        assert (line["decision"], line["caller"], line["game_id"], line["viewer"]) == (
            "allowed",
            "viewer",
            "viewer-test",
            "player_white",
        )
        assert line["fen"] == chess.STARTING_FEN

    def test_the_given_fen_is_what_the_guard_sees(
        self, client: TestClient, games_dir: Path, mock_engine, engine_log: Path
    ) -> None:
        # With fen, the guard (and the log) get that position, not the game's.
        _create_game(games_dir, game_id="done", result="1-0")
        r = client.get("/api/engine/live?game_id=done&depth=5&fen=" + quote(AFTER_E4_E5))
        assert r.status_code == 200
        assert _log(engine_log)[0]["fen"] == AFTER_E4_E5

    def test_an_invalid_fen_is_400(self, client: TestClient, games_dir: Path, mock_engine) -> None:
        _create_game(games_dir, result="1-0")
        r = client.get("/api/engine/live?game_id=viewer-test&depth=5&fen=not-a-fen")
        assert r.status_code == 400

    def test_unknown_viewer_role_400(
        self, client: TestClient, games_dir: Path, mock_engine
    ) -> None:
        _create_game(games_dir)
        r = client.get("/api/engine/live?game_id=viewer-test&viewer=intruder&depth=5")
        assert r.status_code == 400


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
