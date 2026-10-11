"""Route for the ``elo`` domain : current rating board as JSON.

One endpoint :

* ``GET /api/elo`` — rating board w/ cache-is-stale check.

Routes are mounted at the root of the FastAPI app (no prefix) to
preserve the exact URL contract of the pre-split monolith.
"""

from __future__ import annotations

from fastapi import APIRouter

from palamede_chessboard.chess_elo import (
    cache_is_stale,
    load_cached_ratings,
    rebuild_and_persist_cache,
)
from palamede_chessboard.viewer.models import _games_dir


def build_elo_router() -> APIRouter:
    """Return the ``elo`` ``APIRouter`` ready to mount on ``build_app``."""
    router = APIRouter(tags=["elo"])

    @router.get("/api/elo")
    def elo_endpoint() -> dict:
        """Return the current Elo rating board as JSON.

        Design (CD review PR #295 cycles 1-3, 2026-10-05) :

        * cycle 1 — the cache was only refreshed by ``chess_get_elo`` and
          the CLI ``chess-elo rebuild``, never at game termination.
          :func:`cache_is_stale` fixes that.
        * cycle 2 — race rebuild/écriture + delete detection first
          attempted via ``os.utime(games_dir)`` after save, but that
          raised ``EPERM`` silently when the viewer did not own the
          dir (arbiter viewer, magma-owned ``/Users/Shared/games``),
          triggering a rebuild loop. **Approach dropped.**
        * cycle 3 (current) — no ``utime`` at all. The cache file
          stores ``source_mtime`` (``max(mtime(*.pgn))`` captured
          BEFORE the rebuild) and ``source_pgns`` (sorted stems in
          the dir at rebuild time). :func:`cache_is_stale` compares
          these snapshots to the live state : newer mtime → updates,
          different stem set → add/delete/rename. Immune to filesystem
          ownership. One warning per process on write failure.

        Players are sorted by rating desc, then by games desc as a
        tiebreaker (more games = more confidence at same rating),
        then name asc for stable ordering.
        """
        games_dir = _games_dir()
        if cache_is_stale(games_dir=games_dir):
            board, _ = rebuild_and_persist_cache(games_dir=games_dir)
            source = "rebuild"
        else:
            cached = load_cached_ratings(games_dir=games_dir)
            if cached is None:
                board, _ = rebuild_and_persist_cache(games_dir=games_dir)
                source = "rebuild"
            else:
                board = cached
                source = "cache"
        players_sorted = [
            {"name": name, "rating": pr.rating, "games": pr.games}
            for name, pr in sorted(
                board.players.items(),
                key=lambda kv: (-kv[1].rating, -kv[1].games, kv[0]),
            )
        ]
        return {
            "source": source,
            "players": players_sorted,
            "history": [u.to_dict() for u in board.history],
        }

    return router
