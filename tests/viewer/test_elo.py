"""Tests for the viewer elo router : /api/elo (cache + fallback + sort)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl


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
