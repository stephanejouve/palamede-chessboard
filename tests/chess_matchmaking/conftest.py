"""Shared helpers for ``tests/chess_matchmaking``.

Helpers extracted from the monolithic ``tests/test_chess_matchmaking.py``
(718 lines, split per CD review 2026-10-10 msg 57604 point (b)) : the
PGN writer ``_write_pgn`` is used by scheduling, scan_active_games
and rematch-penalty tests ; the ``_board`` / ``_board_with_games``
constructors are used by every pairing / scheduling / tie-break test.
Keeping them in ``conftest.py`` avoids a cross-file private import.

Both are plain module-level functions (not pytest fixtures) because
each test constructs multiple PGNs / boards with different params —
a fixture would need to return a factory, which is strictly more
verbose for no gain here.
"""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.chess_elo import PlayerRating, RatingBoard


def _write_pgn(
    games_dir: Path,
    *,
    game_id: str,
    white: str,
    black: str,
    result: str,
    date: str = "2026.10.06",
    round_: str = "1",
) -> Path:
    """Dump a minimal rated-server PGN with the given headers.

    Mirrors the ``_create_finished`` / ``_create_live`` helpers in
    :mod:`tests.chess_elo.conftest` but inlined here so the matchmaking
    suite does not take a cross-package dependency on the chess_elo
    test fixtures. The movetext is a legal placeholder (``1. e4 e5``)
    plus the result token — ``collect_rated_games`` and
    ``_scan_active_games`` both only read the headers.
    """
    games_dir.mkdir(parents=True, exist_ok=True)
    headers = {
        "Event": "Test",
        "Site": "Chas, Tiresias",
        "Date": date,
        "Round": round_,
        "White": white,
        "Black": black,
        "Result": result,
        "Rated": "true",
    }
    lines = [f'[{tag} "{value}"]\n' for tag, value in headers.items()]
    text = "".join(lines) + "\n" + f"1. e4 e5 {result}" + "\n"
    path = games_dir / f"{game_id}.pgn"
    path.write_text(text, encoding="utf-8")
    return path


def _board(**players: int) -> RatingBoard:
    """Build a minimal RatingBoard from name→rating kwargs."""
    board = RatingBoard()
    for name, rating in players.items():
        board.players[name] = PlayerRating(rating=rating, games=1)
    return board


def _board_with_games(**players: tuple[int, int]) -> RatingBoard:
    """Same as :func:`_board` but takes ``(rating, games)`` tuples."""
    board = RatingBoard()
    for name, (rating, games) in players.items():
        board.players[name] = PlayerRating(rating=rating, games=games)
    return board
