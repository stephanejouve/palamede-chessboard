"""``palamede-coach recompute-attacked`` (#303) : the stored answers, under the current rule."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from palamede_chessboard.coach import diagnosis
from palamede_chessboard.coach.recompute import recompute_attacked
from palamede_chessboard.coach.store import add_exercises, read_exercises
from palamede_chessboard.players import registry

PLAYER = "human-player"
FEN_4E90 = "2kr3r/pp2bnpp/4p3/2pP2P1/8/2Np2qP/PP6/R1B2K2 w - - 0 1"
#: Nothing attacked : the new answer is the empty set.
FEN_QUIET = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"


def _exercise(eid: str, fen: str, squares: list[str], **extra: object) -> dict:
    return {
        "id": eid,
        "kind": "attacked",
        "fen": fen,
        "side": "white",
        "motif": "hanging_piece",
        "solution_squares": squares,
        "state": "proposed",
        "attempts": [{"ok": False}],
        "game_ids": [],
        **extra,
    }


@pytest.fixture
def gdir(tmp_path: Path) -> Path:
    registry.add_player(tmp_path, PLAYER, kind="human")
    return tmp_path


def _stored(gdir: Path, eid: str) -> dict:
    return next(e for e in read_exercises(gdir, PLAYER) if e["id"] == eid)


def test_a_dry_run_reports_and_writes_nothing(gdir: Path) -> None:
    add_exercises(gdir, PLAYER, [_exercise("a", FEN_4E90, ["h3"])])
    summary = recompute_attacked(gdir)
    assert (summary["checked"], summary["changed"], summary["applied"]) == (1, 1, False)
    assert summary["exercises"] == ["a"]
    assert _stored(gdir, "a")["solution_squares"] == ["h3"]


def test_apply_writes_the_new_answer_and_keeps_the_rest(gdir: Path) -> None:
    add_exercises(gdir, PLAYER, [_exercise("a", FEN_4E90, ["h3"], state="failed")])
    assert recompute_attacked(gdir, apply=True)["changed"] == 1
    stored = _stored(gdir, "a")
    assert stored["solution_squares"] == ["d5", "g5", "h3"]
    assert stored["state"] == "failed" and stored["attempts"] == [{"ok": False}]


def test_it_can_be_run_twice(gdir: Path) -> None:
    add_exercises(gdir, PLAYER, [_exercise("a", FEN_4E90, ["h3"])])
    recompute_attacked(gdir, apply=True)
    assert recompute_attacked(gdir, apply=True)["changed"] == 0


def test_a_solved_exercise_stays_as_it_is(gdir: Path) -> None:
    add_exercises(gdir, PLAYER, [_exercise("a", FEN_4E90, ["h3"], state="solved")])
    summary = recompute_attacked(gdir, apply=True)
    assert (summary["checked"], summary["changed"]) == (0, 0)
    assert _stored(gdir, "a")["solution_squares"] == ["h3"]


def test_a_find_move_exercise_is_not_touched(gdir: Path) -> None:
    add_exercises(gdir, PLAYER, [_exercise("a", FEN_4E90, ["h3"], kind="find_move")])
    assert recompute_attacked(gdir, apply=True)["checked"] == 0
    assert _stored(gdir, "a")["solution_squares"] == ["h3"]


def test_an_answer_that_became_empty_is_counted(gdir: Path) -> None:
    add_exercises(gdir, PLAYER, [_exercise("a", FEN_QUIET, ["e2"])])
    summary = recompute_attacked(gdir, apply=True)
    assert summary["empty"] == 1
    assert _stored(gdir, "a")["solution_squares"] == []


def test_an_unreadable_exercise_is_reported_and_left_alone(gdir: Path) -> None:
    add_exercises(gdir, PLAYER, [_exercise("a", "not a fen", ["h3"])])
    summary = recompute_attacked(gdir, apply=True)
    assert summary["unreadable"] == ["a"] and summary["changed"] == 0
    assert _stored(gdir, "a")["solution_squares"] == ["h3"]


def test_the_command_is_a_dry_run_unless_told_and_prints_no_solution(
    gdir: Path, capsys: pytest.CaptureFixture
) -> None:
    add_exercises(gdir, PLAYER, [_exercise("a", FEN_4E90, ["h3"])])
    assert diagnosis.main(["recompute-attacked", "--games-dir", str(gdir)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["applied"] is False
    assert "h3" not in out and "g5" not in out
    assert _stored(gdir, "a")["solution_squares"] == ["h3"]
    assert diagnosis.main(["recompute-attacked", "--games-dir", str(gdir), "--apply"]) == 0
    assert _stored(gdir, "a")["solution_squares"] == ["d5", "g5", "h3"]
