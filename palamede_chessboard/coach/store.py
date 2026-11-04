"""Where the coach keeps its reports (#95) : ``<games_dir>/.coach/reports/``.

A directory ``0700`` and files ``0600``, like the player registry : a
report names a player and their mistakes. Written atomically, under a
lock, with the helpers of :mod:`palamede_chessboard._atomic`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from palamede_chessboard._atomic import locked, write_json
from palamede_chessboard.chessboard_state import GameState
from palamede_chessboard.paths import _validate_game_id

log = logging.getLogger(__name__)

COACH_DIR_NAME = ".coach"
DIR_MODE = 0o700
FILE_MODE = 0o600


def coach_dir(games_dir: Path) -> Path:
    return games_dir / COACH_DIR_NAME


def report_path(games_dir: Path, game_id: str) -> Path:
    _validate_game_id(game_id)  # the id names a file : never ``../``
    return coach_dir(games_dir) / "reports" / f"{game_id}.json"


def game_key(state: GameState) -> str:
    """What a game is, for the coach : root position, moves and result.

    Not the PGN's bytes : #199, #200 and #201 rewrite older files
    (TimeControl, Termination, [%clk]) without the game changing, and a
    key on the bytes would diagnose it again (Leader, #95).
    """
    content = {
        "fen": state.board.root().fen(),
        "moves": [move.uci() for move in state.board.move_stack],
        "result": state.metadata.result,
    }
    raw = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_report(games_dir: Path, game_id: str) -> dict | None:
    """The report of ``game_id``, or ``None`` : absent, or unreadable and to redo."""
    path = report_path(games_dir, game_id)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("coach: report of %s unreadable (%s), diagnosed again", game_id, exc)
        return None
    return report if isinstance(report, dict) else None


def write_report(games_dir: Path, game_id: str, report: dict) -> Path:
    path = report_path(games_dir, game_id)
    for directory in (coach_dir(games_dir), path.parent):
        directory.mkdir(mode=DIR_MODE, exist_ok=True)
        directory.chmod(DIR_MODE)
    with locked(path):
        write_json(path, report, mode=FILE_MODE)
    return path
