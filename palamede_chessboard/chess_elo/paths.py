"""Filesystem path constants for the Elo module.

Isolated so the rest of the package can import ``GAMES_DIR`` without
pulling in any ``chess`` / PGN dependency.
"""

from __future__ import annotations

from pathlib import Path

# TODO Phase 1b : unifier via env var ``MAGMA_GAMES_DIR`` et sortir
# cette constante de la duplication plain avec ``outillages``.
GAMES_DIR = Path("/Users/Shared/games")
