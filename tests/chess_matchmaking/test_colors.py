"""Color-assignment tests : per-pair bias + global white-count tie-break.

Covers :func:`_assign_colors` : the agent who has played more black
against this opponent gets white ; ties fall back to global white
count ; final tie falls back to alphabetical order for determinism.
"""

from __future__ import annotations

from palamede_chessboard.chess_elo import RatedGame
from palamede_chessboard.chess_matchmaking import _assign_colors


class TestAssignColors:
    def test_no_history_alphabetical(self) -> None:
        white, black = _assign_colors([], "junior", "admin")
        assert (white, black) == ("admin", "junior")

    def test_player_who_played_more_black_gets_white(self) -> None:
        # junior was black twice vs admin, admin was black zero times
        # → junior gets white in the new game.
        history = [
            RatedGame(
                game_id="g1",
                white="admin",
                black="junior",
                date="2026.10.01",
                mtime=1.0,
                white_score=1.0,
            ),
            RatedGame(
                game_id="g2",
                white="admin",
                black="junior",
                date="2026.10.02",
                mtime=2.0,
                white_score=0.0,
            ),
        ]
        white, black = _assign_colors(history, "junior", "admin")
        assert (white, black) == ("junior", "admin")

    def test_other_opponents_ignored(self) -> None:
        """Games against a different third player must not pollute the count."""
        history = [
            RatedGame(
                game_id="g1",
                white="junior",
                black="leader",  # <-- not our pair
                date="2026.10.01",
                mtime=1.0,
                white_score=1.0,
            ),
        ]
        white, black = _assign_colors(history, "junior", "admin")
        # No junior-admin history → alphabetical tie-break = admin white.
        assert (white, black) == ("admin", "junior")

    def test_normalize_identity_in_history(self) -> None:
        """A legacy PGN that spells ``DevLeader`` still routes to ``leader``."""
        history = [
            RatedGame(
                game_id="g1",
                white="DevLeader",
                black="admin",
                date="2026.10.01",
                mtime=1.0,
                white_score=1.0,
            ),
        ]
        # Pair history : leader white 1x, admin black 1x. For the next
        # game, the player who has been black MORE in this pair gets
        # white → admin (1 black) > leader (0 black), so admin plays white.
        white, black = _assign_colors(history, "admin", "leader")
        assert (white, black) == ("admin", "leader")


class TestGlobalWhiteTiebreak:
    """CD review 2026-10-06 PR #297 mineur : départage global."""

    def _rated(self, *, white, black):
        return RatedGame(
            game_id=f"{white}-vs-{black}",
            white=white,
            black=black,
            date="2026.10.06",
            mtime=1.0,
            white_score=0.5,
        )

    def test_global_tally_breaks_pair_level_tie(self) -> None:
        """When the pair history is tied, pick by global white count."""
        # admin and junior : no history together at all.
        # BUT admin has already played 3 whites in total (vs other opps),
        # junior has played 0 whites in total.
        # → junior deserves black less (and white more) overall → junior
        # gets white in the new game. Admin stays black.
        history = [
            self._rated(white="admin", black="leader"),
            self._rated(white="admin", black="claude-desktop"),
            self._rated(white="admin", black="leader"),
        ]
        white, black = _assign_colors(history, "admin", "junior")
        assert (white, black) == ("junior", "admin")

    def test_global_tally_tied_falls_back_alphabetical(self) -> None:
        """Pair tied AND global tied → alphabetical final tie-break."""
        history: list[RatedGame] = []
        white, black = _assign_colors(history, "junior", "admin")
        assert (white, black) == ("admin", "junior")
