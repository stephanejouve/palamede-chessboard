"""Where the coach keeps what it found (#95), under ``<games_dir>/.coach/`` :

* ``reports/<game_id>.json`` — the diagnosis of a game, redone at will ;
* ``players/<player_id>.json`` — the exercises of a player and their
  state, which cannot be redone : an unreadable file is never overwritten.

Directories ``0700`` and files ``0600``, like the player registry : both
name a player and their mistakes, and an exercise holds its solution,
which nothing readable by all may show (Admin, #95). Written atomically,
under a lock, with the helpers of :mod:`palamede_chessboard._atomic`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from palamede_chessboard._atomic import locked, write_json
from palamede_chessboard.chessboard_state import GameState
from palamede_chessboard.paths import _validate_game_id
from palamede_chessboard.players import registry

log = logging.getLogger(__name__)

COACH_DIR_NAME = ".coach"
DIR_MODE = 0o700
FILE_MODE = 0o600
EXERCISE_STATES = ("proposed", "solved", "failed")


class CoachStoreError(RuntimeError):
    """A player's exercises cannot be read : never written over."""


def coach_dir(games_dir: Path) -> Path:
    return games_dir / COACH_DIR_NAME


def report_path(games_dir: Path, game_id: str) -> Path:
    _validate_game_id(game_id)  # the id names a file : never ``../``
    return coach_dir(games_dir) / "reports" / f"{game_id}.json"


def player_path(games_dir: Path, player_id: str) -> Path:
    # The registry's id (Admin, #95) : never ``..`` nor ``/`` in the path.
    return coach_dir(games_dir) / "players" / f"{registry.player_id_of(player_id)}.json"


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


def _private_dirs(path: Path) -> None:
    for directory in (path.parent.parent, path.parent):
        directory.mkdir(mode=DIR_MODE, exist_ok=True)
        directory.chmod(DIR_MODE)


def write_report(games_dir: Path, game_id: str, report: dict) -> Path:
    path = report_path(games_dir, game_id)
    _private_dirs(path)
    with locked(path):
        write_json(path, report, mode=FILE_MODE)
    return path


def _exercises_in(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise CoachStoreError(f"{path.name} is unreadable ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("exercises"), list):
        raise CoachStoreError(f"{path.name} has no 'exercises' list")
    return data["exercises"]


def read_exercises(games_dir: Path, player_id: str) -> list[dict]:
    """The exercises of ``player_id`` ; :class:`CoachStoreError` if unreadable."""
    return _exercises_in(player_path(games_dir, player_id))


def add_exercises(games_dir: Path, player_id: str, exercises: list[dict]) -> int:
    """Add the exercises ``player_id`` does not have yet ; how many were added.

    An exercise already there keeps its state and attempts : a game
    diagnosed again, or two games reaching the same position, add nothing.
    """
    path = player_path(games_dir, player_id)
    _private_dirs(path)
    with locked(path):
        current = _exercises_in(path)
        known = {e.get("id") for e in current}
        new = []
        for exercise in exercises:
            if exercise["id"] not in known:
                known.add(exercise["id"])
                new.append(exercise)
        if new:
            write_json(path, {"exercises": current + new}, mode=FILE_MODE)
    return len(new)
