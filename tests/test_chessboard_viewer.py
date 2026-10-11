"""Tests for ``palamede_chessboard.chessboard_viewer`` (Phase C FastAPI endpoints)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.chessboard_viewer import build_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    return TestClient(build_app())


@pytest.fixture
def games_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    return tmp_path


def _create_game(
    games_dir: Path,
    *,
    game_id: str = "viewer-test",
    time_control: TimeControl | None = None,
    result: str = "*",
) -> GameState:
    # Volet 3a : the TimeControl default is now "30+30" (clock-enabled).
    # Viewer tests that don't care about the clock stay deterministic by
    # pinning to "unrated" when the caller doesn't pass a time_control.
    # TestClock explicitly passes "5+3" so it's not affected.
    state = GameState.create(
        GameMetadata(
            game_id=game_id,
            white="w",
            black="b",
            time_control=time_control or TimeControl(format="unrated"),
            result=result,
        ),
        games_dir=games_dir,
    )
    state.metadata.result = result
    state.save()
    return state


class TestHealth:
    def test_health_ok(self, client: TestClient) -> None:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestPositions:
    def test_positions_empty_game(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir)
        r = client.get("/api/positions?game_id=viewer-test")
        assert r.status_code == 200
        body = r.json()
        assert len(body["positions"]) == 1
        assert body["positions"][0]["san"] is None

    def test_positions_with_moves(self, client: TestClient, games_dir: Path) -> None:
        state = _create_game(games_dir)
        state.play_move("e4")
        state.play_move("e5")
        r = client.get("/api/positions?game_id=viewer-test")
        body = r.json()
        assert [p["san"] for p in body["positions"]] == [None, "e4", "e5"]
        assert body["positions"][1]["from"] == "e2"
        assert body["positions"][1]["to"] == "e4"

    def test_missing_game_404(self, client: TestClient) -> None:
        assert client.get("/api/positions?game_id=ghost").status_code == 404


class TestRaw:
    def test_raw_returns_pgn(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir)
        r = client.get("/api/raw?game_id=viewer-test")
        assert r.status_code == 200
        assert '[White "w"]' in r.json()["pgn"]

    def test_raw_missing(self, client: TestClient) -> None:
        assert client.get("/api/raw?game_id=ghost").status_code == 404


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


class TestEtagPositions:
    """PR #122 : ``/api/positions`` supports conditional GET via ETag/304."""

    def test_first_call_returns_etag(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir)
        r = client.get("/api/positions?game_id=viewer-test")
        assert r.status_code == 200
        assert "etag" in {k.lower() for k in r.headers}
        assert r.headers["ETag"].startswith('"')

    def test_matching_if_none_match_returns_304(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir)
        first = client.get("/api/positions?game_id=viewer-test")
        etag = first.headers["ETag"]
        second = client.get(
            "/api/positions?game_id=viewer-test",
            headers={"If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.headers["ETag"] == etag

    def test_state_change_invalidates_etag(self, client: TestClient, games_dir: Path) -> None:
        state = _create_game(games_dir)
        first = client.get("/api/positions?game_id=viewer-test")
        etag_before = first.headers["ETag"]
        state.play_move("e4")
        third = client.get(
            "/api/positions?game_id=viewer-test",
            headers={"If-None-Match": etag_before},
        )
        # ETag changed → fresh 200 (not 304).
        assert third.status_code == 200
        assert third.headers["ETag"] != etag_before


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


class TestHtmlIndex:
    """Phase D : root path serves the Jinja2-rendered HTML UI."""

    def test_root_returns_html(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        assert "<title>Chess Viewer</title>" in body
        assert "/api/board.svg" in body  # JS references the endpoint
        assert "/api/engine/live" in body
        assert "/api/clock" in body

    def test_role_select_renders_all_viewer_roles(self, client: TestClient) -> None:
        # Jinja2 refactor : the role <select> is now generated from
        # VIEWER_ROLES — verify each role appears as an <option>.
        from palamede_chessboard.chessboard_viewer import VIEWER_ROLES

        body = client.get("/").text
        for role in VIEWER_ROLES:
            assert f'<option value="{role}">{role}</option>' in body

    def test_engine_depth_select_marks_default(self, client: TestClient) -> None:
        # Jinja2 refactor : depth options are looped from DEPTH_OPTIONS, and
        # DEFAULT_DEPTH must carry the ``selected`` attribute.
        from palamede_chessboard.chessboard_engine import DEFAULT_DEPTH
        from palamede_chessboard.chessboard_viewer import DEPTH_OPTIONS

        body = client.get("/").text
        for d in DEPTH_OPTIONS:
            if d == DEFAULT_DEPTH:
                assert f'<option value="{d}" selected>{d}</option>' in body
            else:
                assert f'<option value="{d}">{d}</option>' in body


class TestPieceSvg:
    """``/api/piece.svg`` serves a single piece glyph for the captures pane."""

    def test_white_pawn_200(self, client: TestClient) -> None:
        r = client.get("/api/piece.svg?color=w&type=p")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/svg+xml")
        assert "<svg" in r.text
        assert "white" in r.text or "pawn" in r.text

    def test_black_queen_200(self, client: TestClient) -> None:
        r = client.get("/api/piece.svg?color=b&type=q")
        assert r.status_code == 200
        assert "<svg" in r.text

    def test_immutable_cache_header(self, client: TestClient) -> None:
        r = client.get("/api/piece.svg?color=w&type=r")
        assert "immutable" in r.headers.get("cache-control", "")

    def test_invalid_color_400(self, client: TestClient) -> None:
        r = client.get("/api/piece.svg?color=x&type=p")
        assert r.status_code == 400

    def test_invalid_type_400(self, client: TestClient) -> None:
        r = client.get("/api/piece.svg?color=w&type=xyz")
        assert r.status_code == 400


class TestBoardSvg:
    """Phase D : ``/api/board.svg`` renders the position at ply N."""

    def test_initial_position(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir)
        r = client.get("/api/board.svg?game_id=viewer-test&ply=0&size=200")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/svg+xml")
        assert "<svg" in r.text

    def test_ply_after_e4(self, client: TestClient, games_dir: Path) -> None:
        state = _create_game(games_dir)
        state.play_move("e4")
        r = client.get("/api/board.svg?game_id=viewer-test&ply=1&size=200")
        assert r.status_code == 200
        # SVG contains the lastmove highlight (chess.svg uses a colored rectangle)
        assert "<svg" in r.text
        # Verify the e4 square is highlighted as lastmove
        assert "e4" in r.text or "e2" in r.text  # squares referenced by chess.svg

    def test_ply_clamps_to_history_end(self, client: TestClient, games_dir: Path) -> None:
        state = _create_game(games_dir)
        state.play_move("e4")
        # Requesting a ply beyond the history just renders the latest position
        # (loop breaks naturally; no IndexError).
        r = client.get("/api/board.svg?game_id=viewer-test&ply=999&size=200")
        assert r.status_code == 200

    def test_missing_game_404(self, client: TestClient) -> None:
        r = client.get("/api/board.svg?game_id=ghost")
        assert r.status_code == 404


class TestPostMove:
    """#247 : POST /api/games/<id>/move — drag&drop input path.

    The endpoint mirrors the MCP tool ``chess_play_move`` (same
    validation, same PGN write). ``uci`` is converted to SAN
    server-side so the client can send raw drag-drop coordinates.
    """

    def test_post_san_accepted(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir, game_id="mv-san")
        r = client.post("/api/games/mv-san/move", json={"san": "e4"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["move"]["san"] == "e4"
        assert body["state"]["ply"] == 1
        assert body["state"]["turn"] == "black"

    def test_post_uci_converted_to_san(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir, game_id="mv-uci")
        r = client.post("/api/games/mv-uci/move", json={"uci": "e2e4"})
        assert r.status_code == 200
        body = r.json()
        assert body["move"]["san"] == "e4"

    def test_post_missing_input(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir, game_id="mv-empty")
        r = client.post("/api/games/mv-empty/move", json={})
        assert r.status_code == 400

    def test_post_illegal_san_returns_422(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir, game_id="mv-ill")
        r = client.post("/api/games/mv-ill/move", json={"san": "Ke5"})
        assert r.status_code == 422

    def test_post_illegal_uci_returns_422(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir, game_id="mv-ill-uci")
        # e2e5 is illegal from the starting position.
        r = client.post("/api/games/mv-ill-uci/move", json={"uci": "e2e5"})
        assert r.status_code == 422

    def test_post_malformed_uci_returns_422(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir, game_id="mv-badu")
        r = client.post("/api/games/mv-badu/move", json={"uci": "notauci"})
        assert r.status_code == 422

    def test_post_missing_game_404(self, client: TestClient) -> None:
        r = client.post("/api/games/ghost/move", json={"san": "e4"})
        assert r.status_code == 404

    def test_post_persists_pgn(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir, game_id="mv-persist")
        client.post("/api/games/mv-persist/move", json={"san": "e4"})
        # Reload from disk — the MCP server would see the same file.
        state = GameState.load("mv-persist", games_dir=games_dir)
        assert state.san_history == ["e4"]

    def test_post_by_field_is_audit_only(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir, game_id="mv-by")
        r = client.post(
            "/api/games/mv-by/move",
            json={"san": "e4", "by": "human-drag-drop"},
        )
        assert r.status_code == 200
        # The ``by`` field flows through ``play_move`` but doesn't
        # affect state — it's audit-only.
        assert r.json()["move"]["by"] == "human-drag-drop"


class TestGameStreamPayload:
    """#247 : SSE payload unit tests.

    Full end-to-end SSE streaming under starlette's ``TestClient`` is a
    synchronous-vs-async iteration hazard (the client thread blocks on
    ``iter_lines`` while the server generator awaits ``asyncio.sleep``
    → deadlock on close). We unit-test the payload shape here and rely
    on manual browser QA + the frontend ``EventSource`` fallback for the
    streaming path itself.
    """

    def test_payload_shape_at_start(self, games_dir: Path) -> None:
        state = _create_game(games_dir, game_id="pl1")
        from palamede_chessboard.chessboard_viewer import _game_stream_payload

        p = _game_stream_payload(state)
        assert p["game_id"] == "pl1"
        assert p["ply"] == 0
        assert p["turn"] == "white"
        assert p["result"] == "*"
        assert p["san_history"] == []
        assert p["is_game_over"] is False

    def test_payload_after_moves(self, games_dir: Path) -> None:
        state = _create_game(games_dir, game_id="pl2")
        state.play_move("e4")
        state.play_move("e5")
        from palamede_chessboard.chessboard_viewer import _game_stream_payload

        p = _game_stream_payload(state)
        assert p["ply"] == 2
        assert p["turn"] == "white"
        assert p["san_history"] == ["e4", "e5"]
        assert p["is_game_over"] is False

    def test_payload_game_over_via_result(self, games_dir: Path) -> None:
        state = _create_game(games_dir, game_id="pl3")
        state.metadata.result = "1-0"
        from palamede_chessboard.chessboard_viewer import _game_stream_payload

        p = _game_stream_payload(state)
        assert p["is_game_over"] is True
        assert p["result"] == "1-0"


class TestUvicornKwargs:
    """Reverse-proxy aware uvicorn launcher (PR follow-up Phase D)."""

    def test_defaults_safe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "MCP_CHESSBOARD_VIEWER_HOST",
            "MCP_CHESSBOARD_VIEWER_PORT",
            "MCP_CHESSBOARD_VIEWER_FORWARDED_IPS",
        ):
            monkeypatch.delenv(var, raising=False)
        from palamede_chessboard.chessboard_viewer import _uvicorn_kwargs

        kw = _uvicorn_kwargs()
        assert kw["host"] == "127.0.0.1"
        assert kw["port"] == 8765
        assert kw["proxy_headers"] is True
        assert kw["forwarded_allow_ips"] == "127.0.0.1"

    def test_env_overrides_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_CHESSBOARD_VIEWER_HOST", "0.0.0.0")
        monkeypatch.setenv("MCP_CHESSBOARD_VIEWER_PORT", "9001")
        monkeypatch.setenv("MCP_CHESSBOARD_VIEWER_FORWARDED_IPS", "192.168.1.78")
        from palamede_chessboard.chessboard_viewer import _uvicorn_kwargs

        kw = _uvicorn_kwargs()
        assert kw["host"] == "0.0.0.0"
        assert kw["port"] == 9001
        assert kw["forwarded_allow_ips"] == "192.168.1.78"
        assert kw["proxy_headers"] is True

    def test_wildcard_forwarded_ips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_CHESSBOARD_VIEWER_FORWARDED_IPS", "*")
        from palamede_chessboard.chessboard_viewer import _uvicorn_kwargs

        kw = _uvicorn_kwargs()
        assert kw["forwarded_allow_ips"] == "*"


class TestGamesList:
    def test_games_empty(self, client: TestClient, games_dir: Path) -> None:
        r = client.get("/api/games")
        assert r.status_code == 200
        body = r.json()
        assert body == {"in_progress": [], "completed": []}

    def test_games_lists_in_progress_and_completed(
        self, client: TestClient, games_dir: Path
    ) -> None:
        _create_game(games_dir, game_id="live-1", result="*")
        _create_game(games_dir, game_id="live-2", result="*")
        _create_game(games_dir, game_id="done-1", result="1-0")

        body = client.get("/api/games").json()
        in_ids = [g["game_id"] for g in body["in_progress"]]
        done_ids = [g["game_id"] for g in body["completed"]]
        assert set(in_ids) == {"live-1", "live-2"}
        assert done_ids == ["done-1"]
        # each entry carries white/black/ply/result/mtime for the UI label
        for g in body["in_progress"] + body["completed"]:
            for field in ("game_id", "white", "black", "ply", "result", "mtime"):
                assert field in g, f"missing {field} in {g}"

    def test_games_sorted_by_mtime_desc(self, client: TestClient, games_dir: Path) -> None:
        import os
        import time

        _create_game(games_dir, game_id="old", result="*")
        # Force a mtime gap so ordering is deterministic on fast fs.
        time.sleep(0.01)
        _create_game(games_dir, game_id="new", result="*")
        # Explicitly bump old down.
        old_pgn = games_dir / "old.pgn"
        os.utime(old_pgn, (old_pgn.stat().st_atime, old_pgn.stat().st_mtime - 10))

        body = client.get("/api/games").json()
        ids = [g["game_id"] for g in body["in_progress"]]
        assert ids == ["new", "old"]

    def test_games_survives_unreadable_pgn(self, client: TestClient, games_dir: Path) -> None:
        """A single unreadable file shouldn't 500 the whole listing.

        python-chess's PGN parser is permissive — random text often
        parses to an empty game rather than raising — so we can't
        assert the bad entry is filtered. We only require that the
        endpoint doesn't crash and that a valid neighbor still shows.
        """
        _create_game(games_dir, game_id="valid", result="*")
        # File the reader will genuinely fail to open (broken symlink).
        (games_dir / "broken.pgn").symlink_to(games_dir / "does-not-exist.pgn")

        r = client.get("/api/games")
        assert r.status_code == 200
        ids = [g["game_id"] for g in r.json()["in_progress"]]
        assert "valid" in ids


class TestElo:
    """Elo endpoint — reads cache, falls back to rebuild, sorts correctly."""

    def _finish(
        self, games_dir: Path, *, game_id: str, white: str, black: str, result: str
    ) -> None:
        state = GameState.create(
            GameMetadata(
                game_id=game_id,
                white=white,
                black=black,
                time_control=TimeControl(format="unrated"),
            ),
            games_dir=games_dir,
        )
        state.metadata.result = result
        state.save()

    def test_elo_empty_returns_empty_list(self, client: TestClient) -> None:
        r = client.get("/api/elo")
        assert r.status_code == 200
        body = r.json()
        assert body["players"] == []
        assert body["history"] == []
        assert body["source"] in {"cache", "rebuild"}

    def test_elo_rebuilds_when_no_cache(self, client: TestClient, games_dir: Path) -> None:
        """No cache on disk → endpoint rebuilds and reports source=rebuild."""
        self._finish(games_dir, game_id="g1", white="junior", black="admin", result="1-0")
        r = client.get("/api/elo")
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "rebuild"
        names = [p["name"] for p in body["players"]]
        assert set(names) == {"junior", "admin"}
        # Winner must be ranked first (sort by rating desc).
        assert body["players"][0]["name"] == "junior"
        assert body["players"][0]["rating"] > body["players"][1]["rating"]

    def test_elo_sort_rating_desc_then_games_desc(
        self, client: TestClient, games_dir: Path
    ) -> None:
        """Tiebreaker : at equal rating, more games ranks higher (confidence)."""
        # CD nit PR #295 : commentaire corrigé. g1/g2 = deux nulles
        # junior-admin, g3 = nulle admin-leader (pas une victoire). Les
        # trois nulles laissent tout le monde à 1500 mais admin a 3 games,
        # junior 2, leader 1 ; le tri par games desc classe admin > junior
        # > leader.
        self._finish(games_dir, game_id="g1", white="junior", black="admin", result="1/2-1/2")
        self._finish(games_dir, game_id="g2", white="admin", black="junior", result="1/2-1/2")
        self._finish(games_dir, game_id="g3", white="admin", black="leader", result="1/2-1/2")
        r = client.get("/api/elo")
        body = r.json()
        # Three players should appear.
        names_in_order = [p["name"] for p in body["players"]]
        # At equal rating (all draws vs equally-rated opponents = 1500 for everyone),
        # the one with the most games sorts first.
        admin_pos = names_in_order.index("admin")
        junior_pos = names_in_order.index("junior")
        leader_pos = names_in_order.index("leader")
        admin_games = body["players"][admin_pos]["games"]
        junior_games = body["players"][junior_pos]["games"]
        leader_games = body["players"][leader_pos]["games"]
        assert admin_games == 3
        assert junior_games == 2
        assert leader_games == 1
        assert admin_pos < junior_pos < leader_pos

    def test_elo_stale_cache_detects_pgn_deletion(
        self, client: TestClient, games_dir: Path
    ) -> None:
        """CD cycle 3 PR #295 : delete/rename detection via source_pgns set-diff.

        The cache stores the sorted list of PGN stems that produced it ;
        a deletion changes ``set(current_stems)`` vs ``set(cached.source_pgns)``,
        forcing a rebuild — independent of filesystem mtime semantics
        (so the arbiter viewer under a non-owning user is immune to
        EPERM on ``os.utime``).
        """
        self._finish(games_dir, game_id="g1", white="junior", black="admin", result="1-0")
        r1 = client.get("/api/elo")
        assert r1.json()["source"] == "rebuild"
        # Second call hits the cache (same stems, no newer mtime).
        r2 = client.get("/api/elo")
        assert r2.json()["source"] == "cache"
        # Delete the PGN → set(stems) differs from cached.source_pgns → rebuild.
        (games_dir / "g1.pgn").unlink()
        r3 = client.get("/api/elo")
        assert r3.json()["source"] == "rebuild"
        # Board is now empty (the only game was deleted).
        assert r3.json()["players"] == []

    def test_elo_cache_hits_on_stable_dir(self, client: TestClient, games_dir: Path) -> None:
        """CD cycle 3 PR #295 — regression against the previous utime-loop.

        The previous fix wrote ``os.utime(games_dir, snap)`` after each
        save and silently swallowed EPERM when the viewer did not own
        the dir (real deployment : arbiter viewer, magma-owned dir).
        That triggered a rebuild loop on every poll. The source_mtime+
        source_pgns-in-cache approach has no utime at all → multiple
        polls on an idle dir must all hit the cache.
        """
        self._finish(games_dir, game_id="g1", white="junior", black="admin", result="1-0")
        assert client.get("/api/elo").json()["source"] == "rebuild"
        for _ in range(5):
            assert client.get("/api/elo").json()["source"] == "cache"

    def test_elo_rebuild_snapshot_detects_race_window_write(
        self, client: TestClient, games_dir: Path
    ) -> None:
        """CD cycle 3 PR #295 (a) : race rebuild / écriture.

        A PGN whose mtime lands AFTER the pre-rebuild snapshot (what
        a game written during the rebuild window would look like)
        must trip :func:`cache_is_stale` on the next poll. We force
        the mtime via :func:`os.utime` on the file (which we own) to
        guarantee strict > across coarse-granularity filesystems (HFS+
        on darwin : 1 s granularity).
        """
        import os
        import time

        self._finish(games_dir, game_id="g1", white="junior", black="admin", result="1-0")
        r1 = client.get("/api/elo")
        assert r1.json()["source"] == "rebuild"
        self._finish(games_dir, game_id="g2", white="admin", black="junior", result="1-0")
        future = time.time() + 1.0
        os.utime(games_dir / "g2.pgn", (future, future))
        r2 = client.get("/api/elo")
        assert r2.json()["source"] == "rebuild"
        names = [p["name"] for p in r2.json()["players"]]
        assert set(names) == {"junior", "admin"}

    def test_elo_stale_cache_refreshed_on_new_rated_game(
        self, client: TestClient, games_dir: Path
    ) -> None:
        """CD cycle 1 PR #295 : cache stale → endpoint rebuild lazily.

        Reproduit le défaut rapporté : ``ratings.json`` figé du 2/10 vs
        parties classées écrites après. L'endpoint doit détecter le
        delta et renvoyer le classement à jour, pas le cache d'avant.
        """
        import os
        import time

        from palamede_chessboard.chess_elo import (
            load_cached_ratings,
            rebuild_and_persist_cache,
        )

        # 1) Build + persist a baseline cache from one game (junior wins).
        self._finish(games_dir, game_id="g1", white="junior", black="admin", result="1-0")
        baseline, _ = rebuild_and_persist_cache(games_dir=games_dir)
        junior_before = baseline.players["junior"].rating

        # 2) Write a second rated game and force its mtime strictly
        #    greater than the baseline snapshot so cache_is_stale is
        #    deterministic on 1 s-granularity filesystems.
        self._finish(games_dir, game_id="g2", white="admin", black="junior", result="1-0")
        future = time.time() + 1.0
        os.utime(games_dir / "g2.pgn", (future, future))

        r = client.get("/api/elo")
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "rebuild"

        # 3) Fresh board must reflect both games.
        new_board = load_cached_ratings(games_dir=games_dir)
        assert new_board is not None  # cache was persisted by the endpoint
        junior_after = new_board.players["junior"].rating
        assert junior_after < junior_before
        # 4) Next call hits the fresh cache.
        r2 = client.get("/api/elo")
        assert r2.json()["source"] == "cache"
