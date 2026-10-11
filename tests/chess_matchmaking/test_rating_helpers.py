"""Rating-helpers tests : available players + active-games scan.

Covers the input-building side of the matchmaker : who's canonically
in / out of the pool, and the minimal PGN scanner that replaces
``outillages.chessboard_state.list_games`` for the "busy agents"
input to :func:`schedule_round`.
"""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.chess_matchmaking import (
    PLAYER_POOL,
    _available_players,
)
from tests.chess_matchmaking.conftest import _write_pgn


class TestAvailablePlayers:
    def test_all_four_free(self) -> None:
        available, excluded = _available_players([])
        assert set(available) == set(PLAYER_POOL)
        assert excluded == []

    def test_junior_busy(self) -> None:
        available, excluded = _available_players([{"white": "junior", "black": "leader"}])
        assert set(available) == {"admin", "claude-desktop"}
        assert excluded == ["junior", "leader"]

    def test_non_canonical_name_ignored(self) -> None:
        """A PGN that spells a non-canonical name doesn't block anyone."""
        available, _ = _available_players([{"white": "stranger", "black": "visitor"}])
        assert set(available) == set(PLAYER_POOL)

    def test_devleader_alias_blocks_leader(self) -> None:
        """Normalize_identity routes ``devleader`` to ``leader``."""
        available, _ = _available_players([{"white": "devleader", "black": "admin"}])
        assert set(available) == {"junior", "claude-desktop"}


class TestScanActiveGames:
    """BT-006 V2 : local minimal replacement for ``list_games``."""

    def test_active_games_detected(self, tmp_path: Path) -> None:
        """A PGN with ``Result "*"`` is reported, finished ones skipped."""
        from palamede_chessboard.chess_matchmaking import _scan_active_games

        _write_pgn(tmp_path, game_id="live", white="admin", black="junior", result="*")
        _write_pgn(tmp_path, game_id="done", white="leader", black="admin", result="1-0")
        entries = _scan_active_games(tmp_path)
        assert len(entries) == 1
        assert entries[0]["white"] == "admin"
        assert entries[0]["black"] == "junior"

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        """Non-existent dir yields an empty list, not a crash."""
        from palamede_chessboard.chess_matchmaking import _scan_active_games

        assert _scan_active_games(tmp_path / "does-not-exist") == []

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        from palamede_chessboard.chess_matchmaking import _scan_active_games

        assert _scan_active_games(tmp_path) == []
