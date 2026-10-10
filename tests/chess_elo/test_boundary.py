"""Provisional → established K-factor boundary (:data:`PROVISIONAL_GAMES`)."""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.chess_elo import (
    K_ESTABLISHED,
    K_PROVISIONAL,
    PROVISIONAL_GAMES,
    rebuild_ratings_from_games_dir,
)

from .conftest import _create_finished


class TestProvisionalToEstablishedBoundary:
    def test_k_switches_at_30th_game(self, tmp_path: Path) -> None:
        """Game #31 uses K=20 for the player who just hit PROVISIONAL_GAMES=30.

        Rate the same player 30 wins vs 30 ghost opponents to drain the
        provisional window, then one more game and assert the stored
        update used ``K_ESTABLISHED``.
        """
        for i in range(PROVISIONAL_GAMES):
            _create_finished(
                tmp_path,
                game_id=f"warmup-{i:02d}",
                white="hero",
                black=f"ghost-{i:02d}",
                result="1-0",
                date="2026.01.01",
            )
        # Set deterministic mtimes so the sort order mirrors the sequence.
        import os as _os

        for i in range(PROVISIONAL_GAMES):
            _os.utime(tmp_path / f"warmup-{i:02d}.pgn", (1_000_000 + i, 1_000_000 + i))

        # One more game — this one must apply with K=20 for ``hero``.
        _create_finished(
            tmp_path,
            game_id="post-provisional",
            white="hero",
            black="ghost-30",
            result="1-0",
            date="2026.01.02",
        )
        _os.utime(
            tmp_path / "post-provisional.pgn",
            (1_000_000 + PROVISIONAL_GAMES, 1_000_000 + PROVISIONAL_GAMES),
        )

        board = rebuild_ratings_from_games_dir(games_dir=tmp_path)
        # The 31st update (last entry since sort is chronological) : hero
        # had already played 30 games, so k_factor returns K_ESTABLISHED.
        last = board.history[-1]
        assert last.game_id == "post-provisional"
        assert last.k_white == K_ESTABLISHED
        # The ghost opponent is on its first game → provisional.
        assert last.k_black == K_PROVISIONAL
        # Final board : hero's recorded games count is 31.
        assert board.players["hero"].games == PROVISIONAL_GAMES + 1
