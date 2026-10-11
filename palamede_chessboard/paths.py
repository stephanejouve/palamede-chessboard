"""Filesystem paths and the game-id validation regex.

Split out of :mod:`palamede_chessboard.chessboard_state` in BT-001
commit 2 so :mod:`palamede_chessboard.commentary` can share the
``games_dir`` / commentary-path helpers without pulling in the full
state module. All symbols remain re-exported from
``chessboard_state`` for backward compat.

Dedupe note : :mod:`palamede_chessboard.chess_elo.paths` carries its
own ``GAMES_DIR`` constant (duplicated plain) pending unification
through the ``MAGMA_GAMES_DIR`` env var — tracked in
palamede-chessboard#14.
"""

from __future__ import annotations

import re
from pathlib import Path

from palamede_chessboard.errors import ChessboardError

GAMES_DIR = Path("/Users/Shared/games")
COMMENTARY_DIR_NAME = "commentary"
GAME_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")


def _validate_game_id(game_id: str) -> None:
    if not GAME_ID_PATTERN.match(game_id):
        raise ChessboardError(f"Invalid game_id {game_id!r}: must match {GAME_ID_PATTERN.pattern}")


def _pgn_path(games_dir: Path, game_id: str) -> Path:
    return games_dir / f"{game_id}.pgn"


def _commentary_path(games_dir: Path, game_id: str) -> Path:
    return games_dir / COMMENTARY_DIR_NAME / f"{game_id}.jsonl"
