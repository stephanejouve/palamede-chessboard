"""Filesystem paths and the game-id validation regex.

Split out of :mod:`palamede_chessboard.chessboard_state` in BT-001
commit 2 so :mod:`palamede_chessboard.commentary` can share the
``games_dir`` / commentary-path helpers without pulling in the full
state module. All symbols remain re-exported from
``chessboard_state`` for backward compat.

Palamede#14 : the games directory is now unified. :func:`resolve_games_dir`
reads ``MCP_CHESSBOARD_GAMES_DIR`` at call time (so monkeypatched tests
see the override) with ``/Users/Shared/games`` as documented fallback.
``GAMES_DIR`` is the import-time snapshot kept for the legacy
``games_dir or GAMES_DIR`` pattern. New code should prefer
:func:`resolve_games_dir`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from palamede_chessboard.errors import ChessboardError

#: Fallback when ``MCP_CHESSBOARD_GAMES_DIR`` is unset. Documented default
#: for the whole package (CLI help, installation docs).
_DEFAULT_GAMES_DIR = Path("/Users/Shared/games")


def resolve_games_dir() -> Path:
    """Return the games directory, honoring ``MCP_CHESSBOARD_GAMES_DIR``.

    Called at the moment the directory is needed so a test using
    :func:`pytest.MonkeyPatch.setenv` sees the override, and so the
    daemon / viewer / CLI processes started under a particular env
    pick up the same directory without any import-order dance.
    """
    raw = os.environ.get("MCP_CHESSBOARD_GAMES_DIR", "").strip()
    return Path(raw) if raw else _DEFAULT_GAMES_DIR


#: Import-time snapshot of the games directory. Preserved for the
#: ubiquitous ``games_dir = games_dir or GAMES_DIR`` default pattern
#: scattered across the package. New code wanting a value that honors
#: a mid-process env override should call :func:`resolve_games_dir`
#: at call time (``chess_elo.collect_rated_games`` already does since
#: palamede#14).
GAMES_DIR = resolve_games_dir()
COMMENTARY_DIR_NAME = "commentary"
GAME_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")


def _validate_game_id(game_id: str) -> None:
    if not GAME_ID_PATTERN.match(game_id):
        raise ChessboardError(f"Invalid game_id {game_id!r}: must match {GAME_ID_PATTERN.pattern}")


def _pgn_path(games_dir: Path, game_id: str) -> Path:
    return games_dir / f"{game_id}.pgn"


def _commentary_path(games_dir: Path, game_id: str) -> Path:
    return games_dir / COMMENTARY_DIR_NAME / f"{game_id}.jsonl"
