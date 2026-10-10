"""Tests for :mod:`palamede_chessboard.chess_elo.pgn_scan`.

Covers :func:`collect_rated_games` and :func:`rebuild_ratings_from_games_dir`
plus the ``[Rated "true"]`` perimeter and the real-games scenario
arbitrated by Stéphane 2026-10-03.
"""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.chess_elo import (
    INITIAL_RATING,
    PROVISIONAL_GAMES,
    RATED_HEADER,
    collect_rated_games,
    rebuild_ratings_from_games_dir,
)

from .conftest import _create_finished, _create_live, _write_imported_pgn


class TestCollectRatedGames:
    def test_imported_pgn_without_rated_header_excluded(self, tmp_path: Path) -> None:
        """CD rule : only PGNs with ``[Rated "true"]`` enter the Elo."""
        _create_finished(tmp_path, game_id="server-ok", white="junior", black="admin", result="1-0")
        _write_imported_pgn(
            tmp_path,
            game_id="imported-lichess",
            white="Firouzja, Alireza",
            black="Carlsen, Magnus",
            result="0-1",
        )
        games = collect_rated_games(games_dir=tmp_path)
        assert [g.game_id for g in games] == ["server-ok"]

    def test_in_progress_excluded(self, tmp_path: Path) -> None:
        """Games with Result=* are ignored."""
        _create_finished(tmp_path, game_id="done", white="a", black="b", result="1-0")
        _create_live(tmp_path, game_id="live", white="a", black="b")
        games = collect_rated_games(games_dir=tmp_path)
        assert [g.game_id for g in games] == ["done"]

    def test_sort_by_date_then_mtime(self, tmp_path: Path) -> None:
        """CD rule 3 : chronological end order. Same date → mtime
        tiebreaker (``save()`` rewrites on each move, so mtime ≈ end)."""
        import os
        import time

        # Game A : earlier date.
        _create_finished(
            tmp_path, game_id="a-game", white="a", black="b", result="1-0", date="2026.09.28"
        )
        # Game B : later date.
        _create_finished(
            tmp_path, game_id="b-game", white="a", black="b", result="0-1", date="2026.09.29"
        )
        # Game C : same date as B, but save last so mtime is later → sorts after B.
        time.sleep(0.01)
        _create_finished(
            tmp_path, game_id="c-game", white="a", black="b", result="1-0", date="2026.09.29"
        )
        # Explicit mtimes to make the test robust across fast filesystems.
        os.utime(tmp_path / "a-game.pgn", (1000, 1000))
        os.utime(tmp_path / "b-game.pgn", (2000, 2000))
        os.utime(tmp_path / "c-game.pgn", (3000, 3000))

        games = collect_rated_games(games_dir=tmp_path)
        assert [g.game_id for g in games] == ["a-game", "b-game", "c-game"]

    def test_empty_white_black_rejected(self, tmp_path: Path) -> None:
        """Partial PGNs without identified players don't contribute to ratings."""
        _create_finished(tmp_path, game_id="noplayers", white="", black="", result="1-0")
        games = collect_rated_games(games_dir=tmp_path)
        assert games == []


class TestRebuildRatings:
    def test_two_players_two_games(self, tmp_path: Path) -> None:
        """Junior beats admin, then admin beats junior → each has 2 games,
        and both players' ratings end roughly symmetric around 1500. The
        exact arithmetic gives ±2 (second game's expected score is pulled
        by game 1's rating shift) — this is expected Elo behaviour, not
        a cancel-out."""
        _create_finished(
            tmp_path, game_id="g1", white="junior", black="admin", result="1-0", date="2026.09.28"
        )
        _create_finished(
            tmp_path, game_id="g2", white="admin", black="junior", result="1-0", date="2026.09.29"
        )
        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        assert set(board.players) == {"junior", "admin"}
        assert board.players["junior"].games == 2
        assert board.players["admin"].games == 2
        junior_delta = board.players["junior"].rating - INITIAL_RATING
        admin_delta = board.players["admin"].rating - INITIAL_RATING
        # Symmetric drift : both small, opposite signs, sum to zero (± rounding).
        assert abs(junior_delta) <= 3
        assert abs(admin_delta) <= 3
        assert abs(junior_delta + admin_delta) <= 1

    def test_resignation_counts_as_loss(self, tmp_path: Path) -> None:
        """CD rule 4 : abandon = défaite. Here encoded via ``Result=0-1``
        (that is what :meth:`GameState.resign` sets by default)."""
        _create_finished(
            tmp_path, game_id="resign-game", white="junior", black="admin", result="0-1"
        )
        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        assert board.players["junior"].rating < INITIAL_RATING
        assert board.players["admin"].rating > INITIAL_RATING

    def test_draw_moves_toward_higher_rated(self, tmp_path: Path) -> None:
        """A draw costs the higher-rated player and benefits the lower-rated —
        standard Elo property, verifies the 0.5 encoding."""
        # Seed one win so admin outranks junior before the draw.
        _create_finished(
            tmp_path, game_id="g1", white="admin", black="junior", result="1-0", date="2026.09.28"
        )
        _create_finished(
            tmp_path,
            game_id="g2",
            white="admin",
            black="junior",
            result="1/2-1/2",
            date="2026.09.29",
        )
        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        # After the win, admin > 1500 > junior. The draw then nudges them back toward each other.
        updates = [u for u in board.history if u.game_id == "g2"]
        assert len(updates) == 1
        u = updates[0]
        # White (admin) was higher-rated : loses points on a draw.
        assert u.white_after < u.white_before
        # Black (junior) was lower-rated : gains points on a draw.
        assert u.black_after > u.black_before

    def test_history_preserves_order(self, tmp_path: Path) -> None:
        """Rebuild is reproducible — history matches the sorted collect order."""
        _create_finished(
            tmp_path, game_id="first", white="a", black="b", result="1-0", date="2026.09.01"
        )
        _create_finished(
            tmp_path, game_id="second", white="a", black="b", result="0-1", date="2026.09.02"
        )
        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        assert [u.game_id for u in board.history] == ["first", "second"]


class TestCollectWithRatedHeader:
    def test_fixture_shape(self, tmp_path: Path) -> None:
        """Smoke test on the ``_create_finished`` fixture itself : it must
        emit ``[Rated "true"]`` in the generated PGN so the rest of the
        suite reasons on a realistic input.

        Historical context (CD review palamede#13 BT-006 V1) : before
        extraction this test verified ``outillages.GameState.save``
        posted ``[Rated "true"]`` at the server layer. After extraction
        the fixture writes the PGN directly, so the test is now a
        fixture self-check — the real server-side contract (that
        ``chessboard_state`` stamps ``Rated=true`` on every finished
        game) is covered in BT-001 palamede#6 (admin owner).
        """
        _create_finished(tmp_path, game_id="g1", white="junior", black="leader", result="1-0")
        text = (tmp_path / "g1.pgn").read_text(encoding="utf-8")
        assert f'[{RATED_HEADER} "true"]' in text

    def test_identity_split_consolidated_on_rebuild(self, tmp_path: Path) -> None:
        """Three legacy name variants of Leader must collapse to one fiche."""
        _create_finished(tmp_path, game_id="g1", white="admin", black="Leader", result="1-0")
        _create_finished(tmp_path, game_id="g2", white="admin", black="devleader", result="0-1")
        _create_finished(tmp_path, game_id="g3", white="admin", black="leader", result="1-0")
        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        names = set(board.players.keys())
        assert names == {"admin", "leader"}
        assert board.players["leader"].games == 3

    def test_in_progress_server_game_excluded(self, tmp_path: Path) -> None:
        """A server game with Rated=true but Result=* must not count."""
        _create_live(tmp_path, game_id="live", white="junior", black="admin")
        games = collect_rated_games(games_dir=tmp_path)
        assert games == []


class TestRealGamesScenario:
    """Non-regression on the 9-games perimeter arbitrated by Stéphane 2026-10-03.

    Reproduces each historical server game as a fixture PGN (with the
    canonical names and ``[Rated "true"]`` already applied) plus 3
    imports and 4 in-progress games. The Elo rebuild must count exactly
    9 games across 4 agents.
    """

    HISTORICAL_FIXTURES = (
        # Dates match the Date header on the real PGNs under /Users/Shared/games.
        ("admin-vs-cd-20260930", "admin", "claude-desktop", "0-1", "2026.09.30"),
        ("admin-vs-leader-003", "admin", "leader", "1-0", "2026.09.26"),
        ("admin-vs-leader-004", "admin", "leader", "0-1", "2026.09.26"),
        ("admin-vs-leader-005", "admin", "leader", "1-0", "2026.09.27"),
        ("cd-vs-leader-20260930", "claude-desktop", "leader", "1-0", "2026.09.29"),
        ("junior-vs-admin-001", "junior", "admin", "0-1", "2026.09.28"),
        ("junior-vs-leader-002", "junior", "leader", "0-1", "2026.09.28"),
        ("leader-vs-admin-001", "leader", "admin", "1-0", "2026.05.16"),
        ("leader-vs-admin-002", "leader", "admin", "0-1", "2026.05.29"),
    )

    IMPORT_FIXTURES = (
        ("gundersen-faul-1928", "Gunnar Gundersen", "A H Faul", "1-0"),
        ("karpov-kasparov-1990-g1", "Anatoly Karpov", "Garry Kasparov", "1/2-1/2"),
        ("lichess-broadcast", "Firouzja, Alireza", "Carlsen, Magnus", "1-0"),
    )

    IN_PROGRESS_FIXTURES = (
        ("admin-vs-leader-20260930", "admin", "leader"),
        ("claude-vs-admin-2026-09-27", "claude-desktop", "admin"),
        ("junior-vs-cd-20260930", "junior", "claude-desktop"),
        ("junior-vs-leader-001", "junior", "leader"),
    )

    def _seed_scenario(self, tmp_path: Path) -> None:
        for game_id, white, black, result, date in self.HISTORICAL_FIXTURES:
            _create_finished(
                tmp_path,
                game_id=game_id,
                white=white,
                black=black,
                result=result,
                date=date,
            )
        for game_id, white, black, result in self.IMPORT_FIXTURES:
            _write_imported_pgn(
                tmp_path,
                game_id=game_id,
                white=white,
                black=black,
                result=result,
            )
        for game_id, white, black in self.IN_PROGRESS_FIXTURES:
            _create_live(tmp_path, game_id=game_id, white=white, black=black)

    def test_perimeter_matches_cd_arbitrage(self, tmp_path: Path) -> None:
        self._seed_scenario(tmp_path)
        games = collect_rated_games(games_dir=tmp_path)
        assert len(games) == 9, [g.game_id for g in games]
        counted = {g.game_id for g in games}
        expected = {g[0] for g in self.HISTORICAL_FIXTURES}
        assert counted == expected

    def test_four_canonical_agents_appear(self, tmp_path: Path) -> None:
        self._seed_scenario(tmp_path)
        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        assert set(board.players.keys()) == {
            "admin",
            "leader",
            "junior",
            "claude-desktop",
        }
        # Every player must be well into provisional range.
        for name, pr in board.players.items():
            assert pr.games > 0, name
            assert pr.games < PROVISIONAL_GAMES, name
