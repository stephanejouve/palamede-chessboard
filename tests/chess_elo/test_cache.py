"""Tests for :mod:`palamede_chessboard.chess_elo.cache`."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from palamede_chessboard.chess_elo import (
    INITIAL_RATING,
    RATINGS_FILENAME,
    cache_is_stale,
    load_cached_ratings,
    rebuild_and_persist_cache,
    rebuild_ratings_from_games_dir,
    save_cached_ratings,
)
from palamede_chessboard.chess_elo import cache as _cache_mod

from .conftest import _create_finished


class TestCachedRatings:
    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        _create_finished(tmp_path, game_id="g1", white="junior", black="admin", result="1-0")
        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        path = save_cached_ratings(board, games_dir=tmp_path)
        assert path == tmp_path / RATINGS_FILENAME
        loaded = load_cached_ratings(games_dir=tmp_path)
        assert loaded is not None
        assert loaded.players == board.players
        assert len(loaded.history) == len(board.history)

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert load_cached_ratings(games_dir=tmp_path) is None

    def test_cache_file_is_pretty_json(self, tmp_path: Path) -> None:
        """JSON is human-diffable — a `jq .` guarantee for the audit log."""
        _create_finished(tmp_path, game_id="g1", white="a", black="b", result="1-0")
        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        path = save_cached_ratings(board, games_dir=tmp_path)
        text = path.read_text(encoding="utf-8")
        assert "\n" in text  # multi-line
        parsed = json.loads(text)
        assert "players" in parsed and "history" in parsed


class TestCacheIsDerivedNotSource:
    """CD rule 5 : a corrected or removed PGN must flow through a rebuild
    without any manual edit to ratings.json."""

    def test_removed_pgn_disappears_on_rebuild(self, tmp_path: Path) -> None:
        _create_finished(tmp_path, game_id="to-remove", white="junior", black="admin", result="1-0")
        board1 = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        save_cached_ratings(board1, games_dir=tmp_path)
        # Cache contains the game.
        cached = load_cached_ratings(games_dir=tmp_path)
        assert cached is not None
        assert cached.players["junior"].games == 1

        # Remove the PGN and rebuild — the game disappears from the ratings.
        (tmp_path / "to-remove.pgn").unlink()
        board2 = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        # No players at all (the only game is gone).
        assert board2.players == {}

    def test_edited_pgn_result_reflects_on_rebuild(self, tmp_path: Path) -> None:
        _create_finished(tmp_path, game_id="was-win", white="junior", black="admin", result="1-0")
        board_before = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        assert board_before.players["junior"].rating > INITIAL_RATING

        # Edit the PGN result (correction) by rewriting it with a flipped
        # outcome — equivalent to what ``GameState.save()`` would do after
        # a manual metadata patch in the pre-extraction stack.
        _create_finished(tmp_path, game_id="was-win", white="junior", black="admin", result="0-1")
        board_after = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        assert board_after.players["junior"].rating < INITIAL_RATING
        assert board_after.players["admin"].rating > INITIAL_RATING


class TestCacheRobustness:
    def test_atomic_save_leaves_no_tmp_file(self, tmp_path: Path) -> None:
        _create_finished(tmp_path, game_id="g1", white="junior", black="admin", result="1-0")
        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        save_cached_ratings(board, games_dir=tmp_path)
        # No leftover tmp sidecar from the atomic write path.
        leftovers = list(tmp_path.glob(f".{RATINGS_FILENAME}.*.tmp"))
        assert leftovers == []
        # And the final file is readable JSON.
        payload = json.loads((tmp_path / RATINGS_FILENAME).read_text(encoding="utf-8"))
        assert "players" in payload

    def test_load_handles_corrupt_json(self, tmp_path: Path) -> None:
        """A torn / corrupt cache file must be treated as missing, not raise."""
        (tmp_path / RATINGS_FILENAME).write_text("{partial-json-no-closing-brace", encoding="utf-8")
        assert load_cached_ratings(games_dir=tmp_path) is None

    def test_load_handles_missing_file(self, tmp_path: Path) -> None:
        assert load_cached_ratings(games_dir=tmp_path) is None

    def test_save_preserves_existing_mode(self, tmp_path: Path) -> None:
        """CD regression report 2026-10-03 : save_cached_ratings must NOT
        downgrade the mode from 0o664 to 0o600 via the mkstemp umask."""
        import os as _os

        _create_finished(tmp_path, game_id="g1", white="a", black="b", result="1-0")
        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        cache = tmp_path / RATINGS_FILENAME
        # First save : cache didn't exist, falls back to _DEFAULT_SHARED_MODE (0o664).
        save_cached_ratings(board, games_dir=tmp_path)
        assert cache.stat().st_mode & 0o777 == 0o664
        # Operator chmods to a tighter shared mode manually.
        _os.chmod(cache, 0o660)
        save_cached_ratings(board, games_dir=tmp_path)
        assert (
            cache.stat().st_mode & 0o777 == 0o660
        ), "second save must preserve the pre-existing mode"

    def test_save_fresh_cache_defaults_to_group_writable(self, tmp_path: Path) -> None:
        """A brand-new cache file must be group-writable (0o664) so other
        triade agents on the same group can refresh it. Prevents the 0o600
        regression that locked out stephanejouve/admin on 2026-10-03."""
        _create_finished(tmp_path, game_id="g1", white="a", black="b", result="1-0")
        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        cache = tmp_path / RATINGS_FILENAME
        assert not cache.exists()
        save_cached_ratings(board, games_dir=tmp_path)
        mode = cache.stat().st_mode & 0o777
        assert mode == 0o664, f"expected 0o664, got {oct(mode)}"

    def test_save_overwrites_existing_cache(self, tmp_path: Path) -> None:
        _create_finished(tmp_path, game_id="g1", white="a", black="b", result="1-0")
        board_v1 = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        save_cached_ratings(board_v1, games_dir=tmp_path)
        # Add a second game and re-save : atomic replace must not leave
        # a mix of old + new in the file.
        _create_finished(
            tmp_path, game_id="g2", white="a", black="b", result="0-1", date="2026.09.29"
        )
        board_v2 = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        save_cached_ratings(board_v2, games_dir=tmp_path)
        loaded = load_cached_ratings(games_dir=tmp_path)
        assert loaded is not None
        assert len(loaded.history) == 2


class TestCacheIsStaleAndPersist:
    """CD review palamede#13 (BT-006 V1) : coverage of the staleness
    detector (``cache_is_stale``) and the full rebuild + persist pipeline
    (``rebuild_and_persist_cache``), including the write-failure path
    and the once-per-process warning. The 1 s mtime-granularity
    compromise documented in :func:`cache_is_stale` is pinned by
    :meth:`test_mtime_equal_to_source_mtime_not_stale`."""

    def test_absent_cache_is_stale(self, tmp_path: Path) -> None:
        """No ``ratings.json`` on disk → by convention a rebuild is needed."""
        _create_finished(tmp_path, game_id="g1", white="a", black="b", result="1-0")
        assert cache_is_stale(games_dir=tmp_path) is True

    def test_legacy_cache_without_source_mtime_is_stale(self, tmp_path: Path) -> None:
        """A pre-cycle-3 ``ratings.json`` (no ``source_mtime`` field) must
        be treated as stale so the first access after upgrade triggers a
        rebuild and backfills the source snapshot."""
        _create_finished(tmp_path, game_id="g1", white="a", black="b", result="1-0")
        # Hand-written legacy payload : valid board, but ``source_mtime``
        # is explicitly ``null`` (equivalent to the pre-cycle-3 schema).
        (tmp_path / RATINGS_FILENAME).write_text(
            json.dumps(
                {
                    "players": {"a": {"rating": 1500, "games": 0}},
                    "history": [],
                    "source_mtime": None,
                    "source_pgns": [],
                }
            ),
            encoding="utf-8",
        )
        assert cache_is_stale(games_dir=tmp_path) is True

    def test_rebuild_then_no_change_not_stale(self, tmp_path: Path) -> None:
        """Fresh rebuild captures the current source snapshot — the very
        next ``cache_is_stale`` call must say the cache is up to date."""
        _create_finished(tmp_path, game_id="g1", white="a", black="b", result="1-0")
        _, saved = rebuild_and_persist_cache(games_dir=tmp_path)
        assert saved is True
        assert cache_is_stale(games_dir=tmp_path) is False

    def test_new_pgn_newer_mtime_is_stale(self, tmp_path: Path) -> None:
        """A PGN written AFTER the snapshot (strict ``mtime >``) must
        trigger a stale verdict."""
        _create_finished(tmp_path, game_id="g1", white="a", black="b", result="1-0")
        rebuild_and_persist_cache(games_dir=tmp_path)
        # Freeze the source_mtime so the new PGN is unambiguously newer.
        cached = load_cached_ratings(games_dir=tmp_path)
        assert cached is not None
        anchor = cached.source_mtime
        assert anchor is not None
        # Create a new PGN whose mtime is strictly greater.
        _create_finished(
            tmp_path, game_id="g2", white="a", black="b", result="0-1", date="2026.09.29"
        )
        os.utime(tmp_path / "g2.pgn", (anchor + 10, anchor + 10))
        assert cache_is_stale(games_dir=tmp_path) is True

    def test_deleted_pgn_is_stale(self, tmp_path: Path) -> None:
        """Deleting / renaming a tracked PGN flips the set-diff on
        ``source_pgns`` even when mtime-max does not change."""
        _create_finished(tmp_path, game_id="g1", white="a", black="b", result="1-0")
        _create_finished(
            tmp_path, game_id="g2", white="a", black="b", result="0-1", date="2026.09.29"
        )
        rebuild_and_persist_cache(games_dir=tmp_path)
        assert cache_is_stale(games_dir=tmp_path) is False
        (tmp_path / "g2.pgn").unlink()
        # Keep mtime(g1) unchanged : only the set-diff on source_pgns
        # should flag the staleness.
        assert cache_is_stale(games_dir=tmp_path) is True

    def test_mtime_equal_to_source_mtime_not_stale(self, tmp_path: Path) -> None:
        """Pin the documented 1 s-grain FS compromise : a PGN whose mtime
        equals ``source_mtime`` is NOT stale (strict ``>`` check).

        Freezing the compromise with a test prevents a regression that
        would flip the comparison to ``>=`` and reintroduce the rebuild
        loop on HFS+ (CD review 2026-10-05 PR #295 cycle 4)."""
        _create_finished(tmp_path, game_id="g1", white="a", black="b", result="1-0")
        rebuild_and_persist_cache(games_dir=tmp_path)
        cached = load_cached_ratings(games_dir=tmp_path)
        assert cached is not None
        assert cached.source_mtime is not None
        # Force the mtime of the only PGN to equal the snapshot — the
        # strict ``>`` check must leave the cache fresh.
        os.utime(tmp_path / "g1.pgn", (cached.source_mtime, cached.source_mtime))
        assert cache_is_stale(games_dir=tmp_path) is False

    def test_write_failure_returns_saved_false_warns_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``rebuild_and_persist_cache`` must return ``(board, False)``
        and log exactly one warning across TWO consecutive failing calls
        (``_cache_write_warned`` is reset to False by the autouse fixture
        in ``conftest.py``)."""
        _create_finished(tmp_path, game_id="g1", white="a", black="b", result="1-0")

        def _boom(_board, games_dir=None):
            raise OSError("disk full")

        monkeypatch.setattr(_cache_mod, "save_cached_ratings", _boom)
        caplog.set_level(logging.WARNING, logger="palamede_chessboard.chess_elo.cache")

        board1, saved1 = rebuild_and_persist_cache(games_dir=tmp_path)
        assert saved1 is False
        assert board1.players  # fresh board still built, with players seeded
        # Second call : board rebuilt, write still fails, but the warning
        # must NOT be logged a second time (one-per-process flag).
        board2, saved2 = rebuild_and_persist_cache(games_dir=tmp_path)
        assert saved2 is False
        assert board2.players

        write_warnings = [
            r
            for r in caplog.records
            if r.name == "palamede_chessboard.chess_elo.cache"
            and "cache refresh failed" in r.getMessage()
        ]
        assert len(write_warnings) == 1, [r.getMessage() for r in write_warnings]
