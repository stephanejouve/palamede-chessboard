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

from palamede_chessboard.paths import GAMES_DIR, _commentary_path, _validate_game_id


def post_commentary(
    game_id: str,
    *,
    ply: int,
    source: str,
    text: str,
    games_dir: Path | None = None,
) -> dict:
    """Append one commentary line to the per-game JSONL side-car."""
    _validate_game_id(game_id)
    games_dir = games_dir or GAMES_DIR
    path = _commentary_path(games_dir, game_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "ply": int(ply),
        "source": source,
        "text": text,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False))
        f.write("\n")
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
