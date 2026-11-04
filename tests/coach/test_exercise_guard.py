"""An exercise's position, its solution and the engine guard (reviews #204).

* Leader : the guard ignores the opening of a game from the initial
  position, by ply ; an exercise from move 5 starts from a FEN at ply 7,
  so the opening threshold let the MCP give its solution. A game that
  starts from a position has no opening : all its positions count.
* Admin : an exercise is an oracle (409 or 200 tells the engine's move).
  While a live game went through its position — opening and mirror
  included — start, the move and the answer are refused, and logged.
"""

from __future__ import annotations

import json
from pathlib import Path

import chess
import pytest

from palamede_chessboard.chess_engine_guard import (
    MASKED_CLASSIFICATION,
    check_engine_call,
    is_exercise_game,
    live_game_through,
    mask_walk_entries,
)
from palamede_chessboard.chessboard_engine import GameWalkEntry
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.coach import play
from palamede_chessboard.coach.store import add_exercises, read_exercises

HUMAN = "human-player"
#: 1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6, white to move : 4.Qxf7# (4.a3 missed it).
OPENING = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6"]


def _fen(moves: list[str]) -> str:
    board = chess.Board()
    for san in moves:
        board.push_san(san)
    return board.fen()


BEFORE_MATE = _fen(OPENING)
EXERCISE = {
    "id": "find-1",
    "kind": "find_move",
    "source_game_id": "source",
    "ply": 7,
    "fen": BEFORE_MATE,
    "side": "white",
    "motif": "missed_mate",
    "solution_uci": "h5f7",
    "solution_san": "Qxf7#",
    "gap_cp": 29000,
    "slower_mates": [],
    "state": "proposed",
    "attempts": [],
    "game_ids": [],
}
ATTACKED = {**EXERCISE, "id": "att-1", "kind": "attacked", "solution_squares": ["e5"]}


def _game(gdir: Path, game_id: str, moves: list[str], fen: str | None = None) -> GameState:
    meta = GameMetadata(
        game_id=game_id, white="w", black="b", time_control=TimeControl(format="unrated")
    )
    state = GameState.create(meta, games_dir=gdir, starting_fen=fen)
    for san in moves:
        state.play_move(san)
    return state


def _mirror(fen: str) -> str:
    return chess.Board(fen).mirror().fen()


# --- Leader : the guard sees an exercise from its first position --------------


def test_an_exercise_from_move_5_is_guarded(tmp_path: Path) -> None:
    exo = _game(tmp_path, "exo.human-player.20261104-200000-abcd", [], fen=BEFORE_MATE)
    assert exo.board.ply() < 12  # the FEN says move 4 : inside the opening threshold
    for fen in (BEFORE_MATE, _mirror(BEFORE_MATE)):
        verdict = check_engine_call(tmp_path, tool="chess_analyze_position", fen=fen)
        assert verdict["allowed"] is False
        assert verdict["matched_game_id"] == exo.metadata.game_id


def test_any_game_from_a_position_is_guarded_whole(tmp_path: Path) -> None:
    _game(tmp_path, "set-up", ["a3"], fen=BEFORE_MATE)  # its first position counts
    verdict = check_engine_call(tmp_path, tool="chess_analyze_position", fen=BEFORE_MATE)
    assert verdict["allowed"] is False and verdict["matched_game_id"] == "set-up"


def test_every_position_of_a_set_up_game_counts(tmp_path: Path) -> None:
    _game(tmp_path, "set-up-2", ["a3", "a6", "Nc3"], fen=BEFORE_MATE)
    middle = _fen([*OPENING, "a3"])  # neither the first position nor the current one
    verdict = check_engine_call(tmp_path, tool="chess_analyze_position", fen=middle)
    assert verdict["allowed"] is False and verdict["matched_game_id"] == "set-up-2"


def test_an_exercise_game_is_whole_whatever_its_start(tmp_path: Path) -> None:
    # Never made by the coach, but the id alone says « no opening ».
    _game(tmp_path, "exo.human-player.y", ["e4", "e5"])
    verdict = check_engine_call(tmp_path, tool="chess_analyze_position", fen=_fen(["e4"]))
    assert verdict["matched_game_id"] == "exo.human-player.y"
    assert is_exercise_game("EXO.human-player.y")


def test_the_opening_of_a_normal_game_stays_free(tmp_path: Path) -> None:
    _game(tmp_path, "live", OPENING)
    assert check_engine_call(tmp_path, tool="chess_analyze_position", fen=BEFORE_MATE)["allowed"]


def test_the_source_game_is_guarded_in_its_opening(tmp_path: Path) -> None:
    source = _game(tmp_path, "source", [*OPENING, "a3"])
    source.resign(by="white")
    _game(tmp_path, "exo.human-player.x", [], fen=BEFORE_MATE)
    verdict = check_engine_call(tmp_path, tool="chess_analyze_position", game_id="source", ply=6)
    assert verdict["allowed"] is False and verdict["matched_game_id"] == "exo.human-player.x"
    # The walk of the source game is masked at that ply, opening or not.
    walk = [
        GameWalkEntry(
            7, "white", "a3", BEFORE_MATE, _fen([*OPENING, "a3"]), 0, -500, 500, "blunder"
        )
    ]
    masked, count, details = mask_walk_entries(walk, tmp_path)
    assert count == 1 and masked[0].classification == MASKED_CLASSIFICATION
    assert details == [{"ply": 7, "matched_game_id": "exo.human-player.x"}]


def test_a_normal_games_opening_is_not_masked(tmp_path: Path) -> None:
    _game(tmp_path, "live", OPENING)
    walk = [
        GameWalkEntry(
            7, "white", "a3", BEFORE_MATE, _fen([*OPENING, "a3"]), 0, -500, 500, "blunder"
        )
    ]
    assert mask_walk_entries(walk, tmp_path)[1] == 0


# --- Admin : the exercise is no oracle while its position is in play ----------


@pytest.fixture
def gdir(tmp_path: Path) -> Path:
    add_exercises(tmp_path, HUMAN, [dict(EXERCISE), dict(ATTACKED)])
    return tmp_path


def _log(engine_log: Path) -> list[dict]:
    if not engine_log.exists():
        return []
    return [json.loads(line) for line in engine_log.read_text(encoding="utf-8").splitlines()]


def test_live_game_through_takes_the_opening_and_the_mirror(gdir: Path) -> None:
    _game(gdir, "live", OPENING)  # move 5 : inside the opening threshold
    assert live_game_through(BEFORE_MATE, gdir) == "live"
    assert live_game_through(_mirror(BEFORE_MATE), gdir) == "live"
    assert live_game_through(chess.STARTING_FEN, gdir) == "live"  # its first position too


def test_live_game_through_leaves_out_exercises_and_finished_games(gdir: Path) -> None:
    _game(gdir, "exo.human-player.x", [], fen=BEFORE_MATE)
    done = _game(gdir, "done", OPENING)
    done.resign(by="white")
    assert live_game_through(BEFORE_MATE, gdir) is None


def test_start_is_refused_while_a_game_is_there(gdir: Path, engine_log: Path) -> None:
    _game(gdir, "live", OPENING)
    with pytest.raises(play.ExerciseError) as refused:
        play.start(gdir, HUMAN, "find-1")
    assert (refused.value.status, refused.value.message) == (409, play.POSITION_IN_PLAY)
    assert not list(gdir.glob("exo.*.pgn"))
    [line] = _log(engine_log)
    assert (line["tool"], line["decision"], line["caller"]) == (
        "coach_exercise",
        "refused",
        "coach",
    )
    assert (line["matched_game_id"], line["fen"]) == ("live", BEFORE_MATE)
    assert (line["exercise_id"], line["action"]) == ("find-1", "start")


def test_the_mirror_refuses_too(gdir: Path) -> None:
    mirrored = _game(gdir, "mirrored", [], fen=_mirror(BEFORE_MATE))
    assert mirrored.metadata.result == "*"
    with pytest.raises(play.ExerciseError):
        play.start(gdir, HUMAN, "find-1")


def test_the_move_is_refused_if_the_position_comes_after_start(
    gdir: Path, engine_log: Path
) -> None:
    game_id = play.start(gdir, HUMAN, "find-1")["game_id"]
    _game(gdir, "live", OPENING)  # reached after the exercise was started
    state = GameState.load(game_id, games_dir=gdir)
    with pytest.raises(play.ExerciseError) as refused:
        play.play_move(state, HUMAN, "Qxf7#", gdir)
    assert refused.value.message == play.POSITION_IN_PLAY
    assert GameState.load(game_id, games_dir=gdir).san_history == []
    assert read_exercises(gdir, HUMAN)[0]["attempts"] == []  # nothing told, nothing counted
    assert [line["action"] for line in _log(engine_log)] == ["move"]


def test_the_answer_is_refused_if_the_position_comes_after_start(
    gdir: Path, engine_log: Path
) -> None:
    game_id = play.start(gdir, HUMAN, "att-1")["game_id"]
    _game(gdir, "live", OPENING)
    with pytest.raises(play.ExerciseError) as refused:
        play.answer(gdir, HUMAN, "att-1", ["e5"])
    assert refused.value.message == play.POSITION_IN_PLAY
    assert GameState.load(game_id, games_dir=gdir).metadata.result == "*"
    assert [line["action"] for line in _log(engine_log)] == ["answer"]


def test_without_a_game_there_the_exercise_plays(gdir: Path, engine_log: Path) -> None:
    _game(gdir, "elsewhere", ["d4", "d5"])
    game_id = play.start(gdir, HUMAN, "find-1")["game_id"]
    state = GameState.load(game_id, games_dir=gdir)
    assert play.play_move(state, HUMAN, "Qxf7#", gdir)["result"] == "1-0"
    assert read_exercises(gdir, HUMAN)[0]["state"] == "solved"
    assert _log(engine_log) == []  # only refusals are logged
