"""Per-game commentary side-car (JSONL).

Split out of :mod:`palamede_chessboard.chessboard_state` in BT-001
commit 2 to keep the state module under 500 lines. Both functions
are re-exported from ``chessboard_state`` for backward compat.

The side-car lives at ``<games_dir>/<COMMENTARY_DIR_NAME>/<game_id>.jsonl``
with one JSON object per line :

::

    {"ts": "2026-10-05T13:00:00+00:00", "ply": 7, "source": "cd", "text": "..."}

Append-only ; the writer never rewrites existing lines, so concurrent
``post_commentary`` calls from different agents interleave without
corrupting each other (POSIX ``O_APPEND`` guarantee on the FS).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from palamede_chessboard.errors import CommentaryRefusedError, GameNotFoundError
from palamede_chessboard.paths import GAMES_DIR, _commentary_path, _pgn_path, _validate_game_id

#: Bounds of one line, and of a game's side-car : the route takes no token yet,
#: and an unbounded append fills the disk (audit of 08/11, C3).
MAX_PLY = 2000
MAX_SOURCE_CHARS = 64
MAX_TEXT_CHARS = 8000
MAX_SIDECAR_BYTES = 2 * 1024 * 1024


def _refuse_out_of_bounds(ply: int, source: str, text: str) -> None:
    """:class:`CommentaryRefusedError` unless the line is within its bounds."""
    if not 0 <= int(ply) <= MAX_PLY:
        raise CommentaryRefusedError(f"ply must be between 0 and {MAX_PLY}")
    if not source or len(source) > MAX_SOURCE_CHARS:
        raise CommentaryRefusedError(f"source must be 1 to {MAX_SOURCE_CHARS} characters")
    if not text or len(text) > MAX_TEXT_CHARS:
        raise CommentaryRefusedError(f"text must be 1 to {MAX_TEXT_CHARS} characters")


def post_commentary(
    game_id: str,
    *,
    ply: int,
    source: str,
    text: str,
    games_dir: Path | None = None,
) -> dict:
    """Append one commentary line to the side-car of an existing game.

    Raises:
        GameNotFoundError: no such game, and no side-car is created for it.
        CommentaryRefusedError: the line is out of bounds, or the side-car full.
    """
    _validate_game_id(game_id)
    games_dir = games_dir or GAMES_DIR
    if not _pgn_path(games_dir, game_id).exists():
        raise GameNotFoundError(f"game not found: {game_id}")  # never the path (#107)
    _refuse_out_of_bounds(ply, source, text)
    path = _commentary_path(games_dir, game_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "ply": int(ply),
        "source": source,
        "text": text,
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    size = path.stat().st_size if path.exists() else 0
    if size + len(line.encode("utf-8")) > MAX_SIDECAR_BYTES:
        raise CommentaryRefusedError(f"commentary of {game_id} is full")
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
    return entry


def get_commentary(
    game_id: str,
    *,
    ply: int | None = None,
    games_dir: Path | None = None,
) -> list[dict]:
    """Return commentary entries (all or filtered by ``ply``)."""
    _validate_game_id(game_id)
    games_dir = games_dir or GAMES_DIR
    path = _commentary_path(games_dir, game_id)
    if not path.exists():
        return []
    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ply is None or entry.get("ply") == ply:
                entries.append(entry)
    return entries
