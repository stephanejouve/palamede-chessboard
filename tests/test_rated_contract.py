"""Contract tests for the `[Rated "true"]` Elo-perimeter marker.

Extracted from :mod:`tests.test_chessboard_state` in the BT-001 PR
follow-up (CD msg 57602) to keep the state test file under 500 lines.

Three guarantees are pinned here :

1. Server-created games stamp ``[Rated "true"]`` on save (default).
2. PGN imports (lichess, GM databases, …) without the header
   round-trip through load+save **without acquiring one**.
3. End-to-end : :func:`palamede_chessboard.chess_elo.collect_rated_games`
   picks up the server game and ignores a co-located import.
"""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl


def _meta(game_id: str = "test-game") -> GameMetadata:
    return GameMetadata(
        game_id=game_id,
        event="Test",
        site="pytest",
        round="1",
        white="white-player",
        black="black-player",
        arbiter="arbiter-x",
        spectators=["s1", "s2"],
        time_control=TimeControl(format="5+3", start_seconds=300, increment_seconds=3),
    )


class TestRatedHeaderContract:
    """`[Rated "true"]` perimeter marker contract (CD review BT-001)."""

    def test_create_then_save_writes_rated_true(self, tmp_path: Path) -> None:
        """Server-created game writes `[Rated "true"]` on save (default)."""
        GameState.create(_meta(), games_dir=tmp_path)
        pgn_text = (tmp_path / "test-game.pgn").read_text(encoding="utf-8")
        assert '[Rated "true"]' in pgn_text

    def test_imported_pgn_without_rated_round_trips_without(self, tmp_path: Path) -> None:
        """A PGN missing the `Rated` header stays unrated through load+save.

        Reproduces the import path (lichess, GM databases) : we drop
        a PGN directly on disk without the header, then load it with
        ``rated=False`` on the metadata, and verify ``save`` does not
        emit the header. Keeps the Elo perimeter stable : only
        **server-written** games count.
        """
        imported_pgn = (
            '[Event "Lichess Classical"]\n'
            '[Site "lichess.org"]\n'
            '[Date "2024.03.14"]\n'
            '[Round "-"]\n'
            '[White "alice"]\n'
            '[Black "bob"]\n'
            '[Result "1-0"]\n'
            "\n"
            "1. e4 e5 2. Nf3 Nc6 1-0\n"
        )
        pgn_path = tmp_path / "imported.pgn"
        pgn_path.write_text(imported_pgn, encoding="utf-8")

        state = GameState.load("imported", games_dir=tmp_path)
        assert state.metadata.rated is False  # header absent → default off

        # Round-trip : re-save and verify Rated header is NOT emitted.
        state.save()
        reloaded_text = pgn_path.read_text(encoding="utf-8")
        assert "[Rated " not in reloaded_text

    def test_collect_rated_games_includes_server_excludes_import(self, tmp_path: Path) -> None:
        """End-to-end : server game ends → `collect_rated_games` sees it ;
        imported PGN in same dir is excluded by the `[Rated "true"]` filter."""
        from palamede_chessboard.chess_elo import collect_rated_games

        # Server-created game ended by resignation (white resigns → 0-1).
        server = GameState.create(_meta("server"), games_dir=tmp_path)
        server.resign(by="white")

        # Imported PGN (no Rated header) dropped in the same dir.
        imported_pgn = (
            '[Event "Lichess Classical"]\n'
            '[Site "lichess.org"]\n'
            '[Date "2024.03.14"]\n'
            '[Round "1"]\n'
            '[White "alice"]\n'
            '[Black "bob"]\n'
            '[Result "1-0"]\n'
            "\n"
            "1. e4 e5 2. Nf3 Nc6 1-0\n"
        )
        (tmp_path / "imported.pgn").write_text(imported_pgn, encoding="utf-8")

        rated = collect_rated_games(games_dir=tmp_path)
        game_ids = {rg.game_id for rg in rated}
        assert "server" in game_ids
        assert "imported" not in game_ids
