"""Elo rating system for the agent chess tournament.

Scope (CD design constraints 2026-10-02, Stéphane arbitrage 2026-10-03) :

* **Perimeter** : only PGNs that carry the ``[Rated "true"]`` header,
  posted at creation by the chessboard-state module. Imports (lichess,
  GM databases, …) never carry it and are filtered out. Historical
  server games get stamped once-for-all via
  :mod:`scripts.stamp_rated_history`.
* **Identities** : player names are normalised via
  :func:`normalize_identity` — ``strip().lower()`` plus a canonical
  alias table (``devleader -> leader``, …) so a same agent is never
  split across casing or legacy variants.
* **Baseline** : all agents start at :data:`INITIAL_RATING` (1500).
* **K-factor** : :data:`K_PROVISIONAL` (40) for the first
  :data:`PROVISIONAL_GAMES` games per player, then
  :data:`K_ESTABLISHED` (20). FIDE assigns K=40 to new / under-18
  players and K=20 as the general case under 2400.
* **Scoring** : Win = 1.0, Draw = 0.5, Loss = 0.0. Resignation is
  already encoded in the PGN ``Result`` (``0-1`` / ``1-0``).
* **Ordering** : games applied in chronological order of *end*. PGN
  has no ``EndDate`` header ; primary key = PGN ``Date`` header
  (start date, YYYY.MM.DD), tiebreaker = PGN mtime.
* **Rebuild** : :data:`ratings.json` is a derived cache, **never**
  edited by hand. :func:`rebuild_ratings_from_games_dir` recomputes
  from the raw PGNs on demand. Cache writes are atomic (tmp +
  ``os.replace``) and :func:`load_cached_ratings` treats
  :class:`json.JSONDecodeError` as a cache miss so a torn write
  concurrent with a read never bubbles up.

Review doctrine : palamede-chessboard = Leader + Admin APPROVE
(outillages = double APPROVE triade applied to this repo as well).
"""

from palamede_chessboard.chess_elo.algorithm import (
    RESULT_TO_WHITE_SCORE,
    apply_single_update,
    expected_score,
    k_factor,
)
from palamede_chessboard.chess_elo.cache import (
    cache_is_stale,
    load_cached_ratings,
    ratings_path,
    rebuild_and_persist_cache,
    save_cached_ratings,
)
from palamede_chessboard.chess_elo.identity import (
    CANONICAL_IDENTITIES,
    IDENTITY_ALIASES,
    normalize_identity,
)
from palamede_chessboard.chess_elo.models import (
    INITIAL_RATING,
    K_ESTABLISHED,
    K_PROVISIONAL,
    PROVISIONAL_GAMES,
    RATED_HEADER,
    RATED_HEADER_TRUE,
    RATINGS_FILENAME,
    PlayerRating,
    RatedGame,
    RatingBoard,
    RatingUpdate,
)
from palamede_chessboard.chess_elo.paths import GAMES_DIR
from palamede_chessboard.chess_elo.pgn_scan import (
    collect_rated_games,
    rebuild_ratings_from_games_dir,
)

__all__ = [
    # paths
    "GAMES_DIR",
    # identity
    "CANONICAL_IDENTITIES",
    "IDENTITY_ALIASES",
    "normalize_identity",
    # models
    "INITIAL_RATING",
    "K_ESTABLISHED",
    "K_PROVISIONAL",
    "PROVISIONAL_GAMES",
    "RATED_HEADER",
    "RATED_HEADER_TRUE",
    "RATINGS_FILENAME",
    "PlayerRating",
    "RatedGame",
    "RatingBoard",
    "RatingUpdate",
    # algorithm
    "RESULT_TO_WHITE_SCORE",
    "apply_single_update",
    "expected_score",
    "k_factor",
    # pgn_scan
    "collect_rated_games",
    "rebuild_ratings_from_games_dir",
    # cache
    "cache_is_stale",
    "load_cached_ratings",
    "ratings_path",
    "rebuild_and_persist_cache",
    "save_cached_ratings",
]
