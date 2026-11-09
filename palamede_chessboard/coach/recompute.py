"""The expected answer of the stored « attacked » exercises, under the current rule (#303).

An exercise keeps the squares computed when it was created : once the rule of
« en prise » changed, the ones already stored expected the old answer. This
one-shot goes through them, and writes only with ``apply``. A solved exercise
stays as it is, and so do attempts and states. The output has counts and
exercise ids only, never a solution.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import chess

from palamede_chessboard.coach.motifs import _hanging
from palamede_chessboard.coach.store import read_exercises, update_exercise
from palamede_chessboard.players import registry


def _new_answer(exercise: dict) -> list[str] | None:
    """The squares the rule expects now, ``None`` when the exercise is not readable."""
    try:
        return sorted(_hanging(chess.Board(exercise["fen"]), exercise["side"]))
    except (KeyError, TypeError, ValueError):
        return None


def _set_answer(exercise: dict, squares: list[str]) -> None:
    exercise["solution_squares"] = squares


def recompute_attacked(games_dir: Path, *, apply: bool = False) -> dict:
    """``{"checked", "changed", "empty", "unreadable", "applied", "exercises"}``.

    ``exercises`` : the ids whose answer differs. ``empty`` : how many of them
    now expect no square at all, which the board and the viewer both accept.
    """
    summary: dict = {
        "checked": 0,
        "changed": 0,
        "empty": 0,
        "unreadable": [],
        "applied": apply,
        "exercises": [],
    }
    for player in sorted(registry.load(games_dir)["players"]):
        for exercise in read_exercises(games_dir, player):
            if exercise.get("kind") != "attacked" or exercise.get("state") == "solved":
                continue
            summary["checked"] += 1
            answer = _new_answer(exercise)
            if answer is None:
                summary["unreadable"].append(exercise.get("id"))
                continue
            if answer == exercise.get("solution_squares"):
                continue
            summary["changed"] += 1
            summary["empty"] += not answer
            summary["exercises"].append(exercise["id"])
            if apply:
                update_exercise(
                    games_dir, player, exercise["id"], partial(_set_answer, squares=answer)
                )
    return summary
