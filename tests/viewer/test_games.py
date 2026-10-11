"""Tests for the viewer games router : /api/raw, /api/games, /api/games/<id>/move + SSE payload."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameState
from tests.viewer.conftest import _create_game


class TestRaw:
    def test_raw_returns_pgn(self, client: TestClient, games_dir: Path) -> None:
        _create_game(games_dir)
        r = client.get("/api/raw?game_id=viewer-test")
        assert r.status_code == 200
        assert '[White "w"]' in r.json()["pgn"]

    def test_raw_missing(self, client: TestClient) -> None:
        assert client.get("/api/raw?game_id=ghost").status_code == 404


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
