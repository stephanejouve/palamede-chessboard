"""Filesystem path constants for the Elo module.

Palamede#14 : previously carried its own ``GAMES_DIR`` constant
duplicating the one in :mod:`palamede_chessboard.paths`. The constant
is now re-exported from that module so the whole package shares a
single source of truth that honors ``MCP_CHESSBOARD_GAMES_DIR``.
"""

from __future__ import annotations

from palamede_chessboard.paths import GAMES_DIR, resolve_games_dir

__all__ = ["GAMES_DIR", "resolve_games_dir"]
