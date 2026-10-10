"""Pure Elo arithmetic (no I/O, trivially unit-testable).

* :func:`expected_score` — FIDE expected score formula.
* :func:`k_factor` — provisional vs established K.
* :func:`apply_single_update` — one game, two ratings → two new ratings.
"""

from __future__ import annotations

import math

from palamede_chessboard.chess_elo.models import (
    K_ESTABLISHED,
    K_PROVISIONAL,
    PROVISIONAL_GAMES,
    PlayerRating,
)

#: Mapping from PGN ``Result`` header to White's score. Any other
#: value (including the in-progress marker ``*``) excludes the game
#: from rating updates.
RESULT_TO_WHITE_SCORE: dict[str, float] = {
    "1-0": 1.0,
    "0-1": 0.0,
    "1/2-1/2": 0.5,
}


def expected_score(rating_a: int, rating_b: int) -> float:
    """FIDE expected score of player A against player B."""
    return 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / 400.0))


def k_factor(games_played: int) -> int:
    """``K_PROVISIONAL`` for the first ``PROVISIONAL_GAMES`` games, else established."""
    return K_PROVISIONAL if games_played < PROVISIONAL_GAMES else K_ESTABLISHED


def apply_single_update(
    white: PlayerRating,
    black: PlayerRating,
    white_score: float,
) -> tuple[PlayerRating, PlayerRating, int, int]:
    """Apply one game to two ratings, returning the two new ratings + the
    K-factors used. White's score drives the update ; Black's score is
    ``1 - white_score`` by construction (zero-sum).
    """
    kw = k_factor(white.games)
    kb = k_factor(black.games)
    ea = expected_score(white.rating, black.rating)
    # Python's built-in ``round`` uses banker's rounding (round-half-to-
    # even). FIDE regulations prescribe round-half-away-from-zero, but
    # the delta only kicks in at exact half-points — in Elo arithmetic
    # that happens at a rate well below the system's own noise for a
    # private tourney, so we keep ``round`` as-is. CD review palamede#13
    # (BT-006 V1) : do NOT swap to ``math.floor(x + 0.5)`` here — a
    # change in rounding mode would silently reshuffle every rating
    # from the historical PGNs on the next rebuild.
    new_white = PlayerRating(
        rating=round(white.rating + kw * (white_score - ea)),
        games=white.games + 1,
    )
    new_black = PlayerRating(
        rating=round(black.rating + kb * ((1 - white_score) - (1 - ea))),
        games=black.games + 1,
    )
    return new_white, new_black, kw, kb
