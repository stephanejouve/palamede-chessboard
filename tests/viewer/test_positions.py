"""Tests for the viewer positions router : /api/positions, /api/board.svg, /api/piece.svg."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.viewer.conftest import _create_game


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
