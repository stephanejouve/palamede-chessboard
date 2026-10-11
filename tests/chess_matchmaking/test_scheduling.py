"""Scheduling tests : :func:`schedule_round` and :func:`schedule_round_from_disk`.

Covers the top-level entry points of the matchmaker : the pure
in-memory :func:`schedule_round` (board + games_in_progress +
history → RoundSchedule), the on-disk convenience wrapper
:func:`schedule_round_from_disk`, and the stale-cache / cache-miss
rebuild paths it owns.
"""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.chess_matchmaking import schedule_round
from tests.chess_matchmaking.conftest import _board, _board_with_games, _write_pgn


class TestScheduleRound:
    def test_four_players_free_two_matches(self) -> None:
        board = _board(
            admin=1500,
            leader=1510,
            junior=1600,
            **{"claude-desktop": 1610},
        )
        schedule = schedule_round(board, games_in_progress=[], history=[])
        assert len(schedule.matches) == 2
        assert schedule.bye is None
        assert schedule.excluded_busy == ()
        # Optimal pairing : (admin, leader) + (junior, claude-desktop).
        pairs = {frozenset({m.white, m.black}) for m in schedule.matches}
        assert pairs == {
            frozenset({"admin", "leader"}),
            frozenset({"claude-desktop", "junior"}),
        }

    def test_three_free_one_bye(self) -> None:
        board = _board_with_games(
            admin=(1500, 10),
            leader=(1500, 10),
            junior=(1500, 2),  # fewest games → bye
            **{"claude-desktop": (1500, 10)},
        )
        schedule = schedule_round(
            board,
            games_in_progress=[{"white": "claude-desktop", "black": "leader"}],
            history=[],
        )
        # claude-desktop + leader busy → available {admin, junior} → no bye.
        assert schedule.bye is None
        assert len(schedule.matches) == 1
        assert set(schedule.excluded_busy) == {"claude-desktop", "leader"}

    def test_odd_count_byes_lowest_games(self) -> None:
        """3 free players, lowest-games sits out."""
        board = _board_with_games(
            admin=(1500, 20),
            leader=(1500, 20),
            junior=(1500, 2),
            **{"claude-desktop": (1500, 20)},
        )
        schedule = schedule_round(
            board,
            # Only claude-desktop in a game → 3 free (admin, leader, junior).
            games_in_progress=[
                {"white": "claude-desktop", "black": "stranger"},  # non-canonical opp
            ],
            history=[],
        )
        # Of (admin, leader, junior) the one with fewest games is junior.
        assert schedule.bye == "junior"
        assert len(schedule.matches) == 1
        assert set(schedule.excluded_busy) == {"claude-desktop"}

    def test_schedule_to_dict_structure(self) -> None:
        board = _board(admin=1500, leader=1600)
        schedule = schedule_round(
            board,
            games_in_progress=[
                {"white": "junior", "black": "claude-desktop"},
            ],
            history=[],
        )
        data = schedule.to_dict()
        assert "matches" in data and "bye" in data and "excluded_busy" in data
        m = data["matches"][0]
        assert set(m.keys()) == {"white", "black", "white_rating", "black_rating", "elo_gap"}
        assert m["elo_gap"] == 100

    def test_unknown_player_defaults_to_initial_rating(self) -> None:
        """An agent absent from the board enters at :data:`INITIAL_RATING`."""
        from palamede_chessboard.chess_elo import INITIAL_RATING

        board = _board(admin=1500)  # leader absent
        schedule = schedule_round(
            board,
            games_in_progress=[
                {"white": "junior", "black": "claude-desktop"},
            ],
            history=[],
        )
        assert len(schedule.matches) == 1
        m = schedule.matches[0]
        pair = {m.white, m.black}
        assert pair == {"admin", "leader"}
        # Leader absent from board → rated at INITIAL_RATING.
        if m.white == "leader":
            assert m.white_rating == INITIAL_RATING
        else:
            assert m.black_rating == INITIAL_RATING


class TestScheduleRoundFromDisk:
    """Integration test — read from a real games_dir and compute a round."""

    def test_empty_dir_schedules_two_matches(self, tmp_path: Path) -> None:
        """No PGNs → all four at 1500 → two matches, no bye, no excluded."""
        from palamede_chessboard.chess_matchmaking import schedule_round_from_disk

        schedule = schedule_round_from_disk(games_dir=tmp_path)
        assert len(schedule.matches) == 2
        assert schedule.bye is None
        assert schedule.excluded_busy == ()
        # All equal 1500 → tie-break : sorted canonical pairs.
        pairs_sorted = sorted(tuple(sorted((m.white, m.black))) for m in schedule.matches)
        assert pairs_sorted == [
            ("admin", "claude-desktop"),
            ("junior", "leader"),
        ]

    def test_cache_miss_triggers_rebuild(self, tmp_path: Path, monkeypatch) -> None:
        """CD msg 57604 point (a) : load_cached_ratings → None → rebuild.

        Scenario : the stale check reports fresh enough (cache file
        exists, source snapshot matches) BUT the follow-up
        ``load_cached_ratings`` call returns ``None`` — e.g. the cache
        file was concurrently truncated, has a schema mismatch from
        a legacy format no longer parseable, or was deleted between
        the two calls. The old code passed ``None`` straight to
        :func:`schedule_round`, which blew up typecheck (``None`` is
        not a ``RatingBoard``) and would AttributeError at runtime.
        The fix reconstructs via :func:`rebuild_ratings_from_games_dir`.
        """
        from palamede_chessboard import chess_matchmaking as mm
        from palamede_chessboard.chess_matchmaking import schedule_round_from_disk

        # Set up a games_dir with 2 valid finished PGNs so the rebuild
        # has real content to work with (ratings will shift from the
        # initial 1500 after processing these games).
        _write_pgn(
            tmp_path,
            game_id="r1-admin-junior",
            white="admin",
            black="junior",
            result="1-0",
            round_="1",
        )
        _write_pgn(
            tmp_path,
            game_id="r2-leader-cd",
            white="leader",
            black="claude-desktop",
            result="0-1",
            round_="2",
        )

        # Force the "cache is fresh, but load returns None" branch :
        # patch cache_is_stale → False (bypass the normal
        # rebuild-and-persist path) AND load_cached_ratings → None.
        monkeypatch.setattr(mm, "cache_is_stale", lambda games_dir=None: False)
        monkeypatch.setattr(mm, "load_cached_ratings", lambda games_dir=None: None)

        # Without the fix, this call would pass ``board=None`` to
        # :func:`schedule_round` and crash on ``board.players.get(...)``.
        schedule = schedule_round_from_disk(games_dir=tmp_path)

        # Rebuild path returned a valid board → full round scheduled.
        assert schedule is not None
        assert len(schedule.matches) == 2
        assert schedule.bye is None
        # Ratings came from the rebuild (not from the None cache) :
        # at least one match should see a non-INITIAL rating because
        # admin won and leader lost in the PGNs we wrote.
        from palamede_chessboard.chess_elo import INITIAL_RATING

        all_ratings = {r for m in schedule.matches for r in (m.white_rating, m.black_rating)}
        assert all_ratings != {
            INITIAL_RATING
        }, "Rebuild should have shifted at least one rating off the initial value"


class TestStaleCacheRebuild:
    """CD review 2026-10-06 PR #297 bloquant 1."""

    def test_schedule_round_from_disk_rebuilds_stale_cache(self, tmp_path: Path) -> None:
        """A cache that exists but is stale must be rebuilt, not reused."""
        import os
        import time

        from palamede_chessboard.chess_elo import (
            load_cached_ratings,
            rebuild_and_persist_cache,
        )
        from palamede_chessboard.chess_matchmaking import schedule_round_from_disk

        # 1) One game, baseline cache persisted.
        _write_pgn(
            tmp_path,
            game_id="r1-admin-junior",
            white="admin",
            black="junior",
            result="1-0",
            round_="1",
        )
        rebuild_and_persist_cache(games_dir=tmp_path)
        before = load_cached_ratings(games_dir=tmp_path)
        admin_before = before.players["admin"].rating

        # 2) Write a second finished game AND force its mtime strictly
        #    greater than the baseline cache's source_mtime (coarse FS
        #    granularity safety).
        _write_pgn(
            tmp_path,
            game_id="r2-junior-admin",
            white="junior",
            black="admin",
            result="1-0",
            round_="2",
        )
        future = time.time() + 1.0
        os.utime(tmp_path / "r2-junior-admin.pgn", (future, future))

        # 3) schedule_round_from_disk must see the second game ; the
        #    cached board was build from one game only, admin's rating
        #    changes after the second (he lost as black).
        schedule = schedule_round_from_disk(games_dir=tmp_path)
        assert schedule is not None
        after = load_cached_ratings(games_dir=tmp_path)
        assert after.players["admin"].rating != admin_before
