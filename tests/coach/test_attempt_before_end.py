"""The winning attempt is recorded before the exercise's game ends (#254).

The viewer reads « Mes exercices » once more when the exo.* game shown ends,
woken by the stream as soon as the finished PGN is saved. Saved before the
attempt, that read found the exercise still « à faire » — and, read once per
game, the panel kept it (review Leader #255). So whoever sees the game
finished finds the attempt already there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.chessboard_state import GameState, IllegalMoveError
from palamede_chessboard.coach import play
from palamede_chessboard.coach.store import add_exercises, read_exercises
from tests.coach.test_exercise_guard import ATTACKED, EXERCISE, HUMAN


@pytest.fixture
def gdir(tmp_path: Path) -> Path:
    add_exercises(tmp_path, HUMAN, [dict(EXERCISE), dict(ATTACKED)])
    return tmp_path


def _states_seen_at_the_end(
    monkeypatch: pytest.MonkeyPatch, gdir: Path, exercise_id: str
) -> list[str]:
    """The exercise's state, read when a finished PGN is saved."""
    seen: list[str] = []
    save = GameState.save

    def spy(self: GameState) -> Path:
        path = save(self)
        if self.metadata.result != "*":
            [e] = [x for x in read_exercises(gdir, HUMAN) if x["id"] == exercise_id]
            seen.append(e["state"])
        return path

    monkeypatch.setattr(GameState, "save", spy)
    return seen


def test_the_right_move_is_counted_before_the_game_ends(
    gdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = play.start(gdir, HUMAN, "find-1")["game_id"]
    seen = _states_seen_at_the_end(monkeypatch, gdir, "find-1")
    state = GameState.load(game_id, games_dir=gdir)
    assert play.play_move(state, HUMAN, "Qxf7#", gdir)["result"] == "1-0"
    # A mate ends the game in play_move, then _finish saves it again : both see it.
    assert len(seen) == 2 and set(seen) == {"solved"}


def test_the_right_squares_are_counted_before_the_game_ends(
    gdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    play.start(gdir, HUMAN, "att-1")
    seen = _states_seen_at_the_end(monkeypatch, gdir, "att-1")
    assert play.answer(gdir, HUMAN, "att-1", ["e5"])["state"] == "solved"
    assert seen == ["solved"]


def test_missed_then_found_is_failed_when_the_game_ends(
    gdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = play.start(gdir, HUMAN, "find-1")["game_id"]
    seen = _states_seen_at_the_end(monkeypatch, gdir, "find-1")
    with pytest.raises(play.ExerciseError):
        play.play_move(GameState.load(game_id, games_dir=gdir), HUMAN, "a3", gdir)
    assert seen == []  # a miss leaves the game open
    play.play_move(GameState.load(game_id, games_dir=gdir), HUMAN, "Qxf7#", gdir)
    # « à reprendre · déjà trouvé » from the first read on.
    assert seen and set(seen) == {"failed"}


def test_a_san_the_board_refuses_counts_nothing(gdir: Path) -> None:
    # Lenient for parse_san, refused by the strict check of play_move :
    # refused as before, and no attempt recorded for it.
    game_id = play.start(gdir, HUMAN, "find-1")["game_id"]
    state = GameState.load(game_id, games_dir=gdir)
    with pytest.raises(IllegalMoveError):
        play.play_move(state, HUMAN, "Qf7", gdir)
    [e] = [x for x in read_exercises(gdir, HUMAN) if x["id"] == "find-1"]
    assert (e["state"], e["attempts"]) == ("proposed", [])
    assert GameState.load(game_id, games_dir=gdir).metadata.result == "*"
