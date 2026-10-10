"""PGN scanning + full ratings rebuild (filesystem I/O).

Reads ``*.pgn`` from a games directory, filters to server-played +
finished games via the ``[Rated "true"]`` perimeter, and applies the
Elo updates in chronological order. ``ratings.json`` is a derived
cache (see :mod:`.cache`) ; this module is the single source of truth.
"""

from __future__ import annotations

from pathlib import Path

import chess.pgn

from palamede_chessboard.chess_elo.algorithm import (
    RESULT_TO_WHITE_SCORE,
    apply_single_update,
)
from palamede_chessboard.chess_elo.identity import normalize_identity
from palamede_chessboard.chess_elo.models import (
    RATED_HEADER,
    RATED_HEADER_TRUE,
    PlayerRating,
    RatedGame,
    RatingBoard,
    RatingUpdate,
)
from palamede_chessboard.chess_elo.paths import GAMES_DIR


def _parse_rated_game(pgn_path: Path) -> RatedGame | None:
    """Return a :class:`RatedGame` iff the PGN qualifies, else ``None``.

    Qualification rules :

    * ``Rated`` header equals ``"true"`` (case-insensitive). Imports
      (lichess, GM databases, …) never carry it, so they are excluded
      by construction. Server games get the header posted by the
      outillages ``GameState.save`` (outillages#295) ; historical ones
      get stamped via the outillages ``scripts.stamp_rated_history`` CLI.
    * ``Result`` header in :data:`RESULT_TO_WHITE_SCORE` (so ``*`` /
      in-progress games don't contribute).
    * ``White`` and ``Black`` both normalise (via
      :func:`normalize_identity`) to non-empty strings. At parse time
      this normalisation is **read-only** — this function does not touch
      the file. The dedicated outillages ``scripts.stamp_rated_history``
      tool is the one explicit path that rewrites ``White`` / ``Black``
      on disk (with backup + atomic write), typically run once post-deploy.
    """
    try:
        with pgn_path.open("r", encoding="utf-8") as f:
            game = chess.pgn.read_game(f)
    except OSError:
        return None
    if game is None:
        return None
    headers = game.headers
    rated_raw = headers.get(RATED_HEADER, "").strip().lower()
    if rated_raw != RATED_HEADER_TRUE:
        return None
    result = headers.get("Result", "").strip()
    if result not in RESULT_TO_WHITE_SCORE:
        return None
    white = normalize_identity(headers.get("White", ""))
    black = normalize_identity(headers.get("Black", ""))
    if not white or not black:
        return None
    try:
        round_num = int(headers.get("Round", "").strip())
    except (ValueError, TypeError):
        round_num = 0
    return RatedGame(
        game_id=pgn_path.stem,
        white=white,
        black=black,
        date=headers.get("Date", "").strip() or "????.??.??",
        mtime=pgn_path.stat().st_mtime,
        white_score=RESULT_TO_WHITE_SCORE[result],
        round=round_num,
    )


def collect_rated_games(games_dir: Path | None = None) -> list[RatedGame]:
    """Scan ``games_dir`` for PGNs, filter to server-played + finished games,
    and return them sorted by (``Date``, mtime, ``game_id``).

    The returned list is the exact order in which ratings should be applied
    for a reproducible rebuild (CD rule 3 — chronological end order).
    """
    games_dir = games_dir or GAMES_DIR
    out: list[RatedGame] = []
    for pgn_path in sorted(games_dir.glob("*.pgn")):
        rated = _parse_rated_game(pgn_path)
        if rated is not None:
            out.append(rated)
    out.sort(key=lambda rg: rg.sort_key)
    return out


def rebuild_ratings_from_games_dir(
    games_dir: Path | None = None,
) -> RatingBoard:
    """Recompute all ratings from the raw PGNs on disk — the one and only
    source of truth per CD rule 5 (``ratings.json`` is a derived cache)."""
    games_dir = games_dir or GAMES_DIR
    board = RatingBoard()
    for rg in collect_rated_games(games_dir=games_dir):
        white = board.players.get(rg.white, PlayerRating.initial())
        black = board.players.get(rg.black, PlayerRating.initial())
        new_white, new_black, kw, kb = apply_single_update(
            white=white, black=black, white_score=rg.white_score
        )
        board.players[rg.white] = new_white
        board.players[rg.black] = new_black
        board.history.append(
            RatingUpdate(
                game_id=rg.game_id,
                white=rg.white,
                black=rg.black,
                white_before=white.rating,
                black_before=black.rating,
                white_after=new_white.rating,
                black_after=new_black.rating,
                white_score=rg.white_score,
                k_white=kw,
                k_black=kb,
            )
        )
    return board
