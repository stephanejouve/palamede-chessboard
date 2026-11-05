"""A player's progress, read from the coach's store (#95, step 2).

Synthetic reports, exercises and games, players named by function ; the
day is injected, never read from the clock.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import chess
import pytest

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.coach import progress as progress_module
from palamede_chessboard.coach.progress import (
    MIN_GAMES,
    REMIND_DAYS,
    WINDOW,
    first_try,
    game_date,
    in_games,
    on_exercises,
    plies_of,
    progress,
    reminder,
    suggestion,
)
from palamede_chessboard.coach.store import add_exercises, coach_dir, write_report

HUMAN, OTHER = "human-player", "other-player"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _report(gdir: Path, game_id: str, day: str, sides: dict, *, written: bool = True) -> None:
    """A report ; ``written=False`` : one from before ``game_date`` and ``plies``."""
    report: dict = {"game_id": game_id, "sides": {}}
    if written:
        report.update(game_date=day, game_date_source="header")
    for side, (player, plies, by_motif) in sides.items():
        found = {"player": player, "mistakes": [], "by_motif": by_motif}
        if written:
            found["plies"] = plies
        report["sides"][side] = found
    write_report(gdir, game_id, report)


def _pgn(gdir: Path, game_id: str, date: str, moves: list[str], fen: str | None = None) -> None:
    meta = GameMetadata(
        game_id=game_id,
        white=HUMAN,
        black=OTHER,
        time_control=TimeControl(format="unrated"),
        date=date,
    )
    state = GameState.create(meta, games_dir=gdir, starting_fen=fen)
    for san in moves:
        state.play_move(san)


def _exercise(eid: str, motif: str = "hanging_piece", **fields) -> dict:
    return {
        "id": eid,
        "kind": "find_move",
        "motif": motif,
        "state": "proposed",
        "attempts": [],
        "game_ids": [],
        "created_at": "2026-11-01T10:00:00+00:00",
        "solution_uci": "a2a3",
        **fields,
    }


def _try(game_id: str, ok: bool, at: str = "2026-11-10T10:00:00+00:00", **extra) -> dict:
    return {"game_id": game_id, "ok": ok, "at": at, **extra}


# --- the date and the moves -------------------------------------------------------


def _state(date: str, moves: list[str], fen: str | None = None) -> GameState:
    state = GameState(
        GameMetadata(game_id="g", white=HUMAN, black=OTHER, date=date),
        chess.Board(fen) if fen else chess.Board(),
    )
    for san in moves:
        state.board.push_san(san)
    return state


def test_the_date_is_the_header() -> None:
    assert game_date(_state("2026.11.05", []), NOW) == ("2026-11-05", "header")


@pytest.mark.parametrize("date", ["????.??.??", "2026.11.??", "", "05/11/2026"])
def test_a_partial_date_is_the_diagnosis_day(date: str) -> None:
    assert game_date(_state(date, []), NOW) == ("2026-11-20", "diagnosis")


def test_the_moves_of_each_side() -> None:
    state = _state("2026.11.05", ["e4", "e5", "Nf3"])
    assert (plies_of(state, "white"), plies_of(state, "black")) == (2, 1)
    black_first = _state("2026.11.05", ["e5", "Nf3"], fen="8/4p3/8/8/8/8/8/K5Nk b - - 0 1")
    assert (plies_of(black_first, "white"), plies_of(black_first, "black")) == (1, 1)


# --- in games ----------------------------------------------------------------------


def _games(count: int, start_day: int = 1, mistakes: int = 1) -> list[dict]:
    games = [
        {
            "date": f"2026-10-{start_day + i:02d}",
            "game_id": f"g{i:02d}",
            "plies": 20,
            "by_motif": {"hanging_piece": mistakes},
        }
        for i in range(count)
    ]
    return sorted(games, key=lambda g: (g["date"], g["game_id"]), reverse=True)


def test_the_last_ten_games_against_the_ten_before() -> None:
    older = _games(WINDOW, start_day=1, mistakes=2)
    newer = _games(WINDOW, start_day=11, mistakes=1)
    for i, game in enumerate(newer):
        game["game_id"] = f"n{i:02d}"
    extra = _games(1, start_day=0, mistakes=9)  # the 21st game, out of both windows
    found = in_games(newer + older + extra)
    assert found["recent"]["games"] == found["before"]["games"] == WINDOW
    assert found["recent"]["moves"] == 20 * WINDOW
    assert found["recent"]["motifs"]["hanging_piece"] == {"mistakes": 10, "per_100_moves": 5.0}
    assert found["before"]["motifs"]["hanging_piece"] == {"mistakes": 20, "per_100_moves": 10.0}
    assert found["recent"]["motifs"]["missed_mate"] == {"mistakes": 0, "per_100_moves": 0.0}
    assert found["enough"] is True


def test_not_enough_games_on_either_side() -> None:
    assert in_games(_games(WINDOW + MIN_GAMES))["enough"] is True
    assert in_games(_games(WINDOW + MIN_GAMES - 1))["enough"] is False
    empty = in_games([])
    assert empty["enough"] is False
    assert empty["recent"]["motifs"]["other"] == {"mistakes": 0, "per_100_moves": None}


def test_only_the_player_s_side_counts(tmp_path: Path) -> None:
    # Two humans : a report for each side (Admin, #95).
    _report(
        tmp_path,
        "duel",
        "2026-11-05",
        {
            "white": (HUMAN, 30, {"hanging_piece": 1}),
            "black": (OTHER, 29, {"hanging_piece": 4, "missed_mate": 2}),
        },
    )
    mine = progress(tmp_path, HUMAN, NOW)["games"]["recent"]
    assert (mine["games"], mine["moves"]) == (1, 30)
    assert mine["motifs"]["hanging_piece"]["mistakes"] == 1
    assert mine["motifs"]["missed_mate"]["mistakes"] == 0


def test_games_are_placed_by_their_date_not_their_diagnosis(tmp_path: Path) -> None:
    _report(tmp_path, "zz-old", "2026-10-01", {"white": (HUMAN, 10, {"other": 1})})
    _report(tmp_path, "aa-new", "2026-11-01", {"white": (HUMAN, 10, {})})
    games = progress_module._games_of(tmp_path, HUMAN)
    assert [g["game_id"] for g in games] == ["aa-new", "zz-old"]


def test_an_older_report_reads_its_pgn(tmp_path: Path) -> None:
    _pgn(tmp_path, "before", "2026.11.03", ["e4", "e5", "Nf3"])
    _report(tmp_path, "before", "", {"white": (HUMAN, 0, {"other": 1})}, written=False)
    _pgn(tmp_path, "undated", "????.??.??", ["e4"])
    _report(tmp_path, "undated", "", {"white": (HUMAN, 0, {"other": 5})}, written=False)
    _report(tmp_path, "gone", "", {"white": (HUMAN, 0, {"other": 7})}, written=False)
    games = progress_module._games_of(tmp_path, HUMAN)
    # Undated and without its PGN : out of the series.
    assert games == [
        {"date": "2026-11-03", "game_id": "before", "plies": 2, "by_motif": {"other": 1}}
    ]


def test_a_stray_file_among_the_reports_is_skipped(tmp_path: Path) -> None:
    _report(tmp_path, "g1", "2026-11-05", {"white": (HUMAN, 10, {})})
    (coach_dir(tmp_path) / "reports" / "bad name!.json").write_text("{}", encoding="utf-8")
    (coach_dir(tmp_path) / "reports" / "broken.json").write_text("{", encoding="utf-8")
    assert [g["game_id"] for g in progress_module._games_of(tmp_path, HUMAN)] == ["g1"]


# --- on exercises ------------------------------------------------------------------


def test_the_first_try_is_on_the_first_presentation() -> None:
    # Wrong, then right in the same game, then a clean second game : the
    # first try stays wrong (Leader, #95).
    retried = _exercise(
        "e",
        state="solved",
        game_ids=["x1", "x2"],
        attempts=[_try("x1", False), _try("x1", True), _try("x2", True)],
    )
    assert first_try(retried) is False
    assert first_try(_exercise("e", game_ids=["x1"], attempts=[_try("x1", True)])) is True


@pytest.mark.parametrize(
    "exercise",
    [
        _exercise("never-started"),
        _exercise("started-not-tried", game_ids=["x1"]),
        # Not tried in its first game : the second game is no first presentation.
        _exercise("tried-later", game_ids=["x1", "x2"], attempts=[_try("x2", True)]),
        # Abandoned before trying : counted apart (Stéphane, 05/11).
        _exercise(
            "abandoned",
            game_ids=["x1", "x2"],
            attempts=[_try("x1", False, abandoned=True), _try("x2", True)],
        ),
    ],
)
def test_no_first_try(exercise: dict) -> None:
    assert first_try(exercise) is None


def test_counts_per_motif() -> None:
    exercises = [
        _exercise("p1"),
        _exercise("ok", state="solved", game_ids=["a"], attempts=[_try("a", True)]),
        _exercise(
            "retry",
            state="solved",
            game_ids=["b", "c"],
            attempts=[_try("b", False), _try("c", True)],
        ),
        _exercise("ko", state="failed", game_ids=["d"], attempts=[_try("d", False)]),
        _exercise(
            "left",
            game_ids=["e", "f"],
            attempts=[_try("e", False, abandoned=True), _try("f", False, abandoned=True)],
        ),
        _exercise("mate", motif="missed_mate"),
    ]
    counts = on_exercises(exercises)
    assert counts["hanging_piece"] == {
        "exercises": 5,
        "proposed": 2,
        "solved": 2,
        "failed": 1,
        "first_try": {"tried": 3, "ok": 1},
        "solved_after_retry": 1,
        "abandoned_games": 2,
    }
    assert counts["missed_mate"]["exercises"] == 1


# --- the suggestion and the reminder -------------------------------------------------


def _with_mistakes(**per_motif: int) -> dict:
    games = [{"date": "2026-11-01", "game_id": "g", "plies": 40, "by_motif": per_motif}]
    return in_games(games)


def test_the_suggestion_follows_the_motif_most_missed() -> None:
    # ignored_threat comes after hanging_piece in MOTIFS : the mistakes rank, not the order.
    exercises = [
        _exercise("hang-old", created_at="2026-11-01T00:00:00+00:00"),
        _exercise("threat-new", motif="ignored_threat", created_at="2026-11-09T00:00:00+00:00"),
        _exercise("threat-old", motif="ignored_threat", created_at="2026-11-02T00:00:00+00:00"),
    ]
    found = suggestion(exercises, _with_mistakes(ignored_threat=3, hanging_piece=1))
    assert found == {"id": "threat-old", "motif": "ignored_threat", "kind": "find_move"}
    # Nothing waiting in that motif : the next one.
    found = suggestion(exercises[:1], _with_mistakes(ignored_threat=3, hanging_piece=1))
    assert found is not None and found["id"] == "hang-old"


def test_the_suggestion_falls_back_on_a_failed_one_and_shows_no_solution() -> None:
    exercises = [
        _exercise("done", state="solved"),
        _exercise("ko", state="failed"),
    ]
    assert suggestion(exercises, _with_mistakes()) == {
        "id": "ko",
        "motif": "hanging_piece",
        "kind": "find_move",
    }
    assert suggestion(exercises[:1], _with_mistakes()) is None


def test_the_reminder_when_exercises_wait_and_none_was_tried() -> None:
    waiting = _exercise("p", created_at="2026-11-10T00:00:00+00:00")
    tried = _exercise(
        "t", state="failed", game_ids=["x"], attempts=[_try("x", False, "2026-11-12T09:00:00")]
    )
    found = reminder([waiting, tried], NOW)
    assert found == {
        "waiting": 1,
        "last_attempt_at": "2026-11-12T09:00:00",
        "days_since_last_attempt": 8,
        "due": True,
    }


def test_no_reminder_while_the_player_is_at_it() -> None:
    waiting = _exercise("p", created_at="2026-11-01T00:00:00+00:00")
    recent = f"2026-11-{20 - REMIND_DAYS + 1:02d}T09:00:00+00:00"
    tried = _exercise("t", state="solved", game_ids=["x"], attempts=[_try("x", True, recent)])
    assert reminder([waiting, tried], NOW)["due"] is False


def test_no_reminder_for_fresh_exercises_or_none() -> None:
    fresh = f"2026-11-{20 - REMIND_DAYS + 1:02d}T00:00:00+00:00"
    assert reminder([_exercise("p", created_at=fresh)], NOW)["due"] is False
    old = f"2026-11-{20 - REMIND_DAYS:02d}T00:00:00+00:00"
    assert reminder([_exercise("p", created_at=old)], NOW)["due"] is True
    assert reminder([], NOW) == {
        "waiting": 0,
        "last_attempt_at": None,
        "days_since_last_attempt": None,
        "due": False,
    }


def test_progress_reads_the_player_s_store(tmp_path: Path) -> None:
    add_exercises(tmp_path, HUMAN, [_exercise("p1")])
    add_exercises(tmp_path, OTHER, [_exercise("theirs", motif="missed_mate")])
    found = progress(tmp_path, HUMAN, NOW)
    assert found["player"] == HUMAN
    assert list(found["exercises"]) == ["hanging_piece"]
    assert found["suggestion"] == {"id": "p1", "motif": "hanging_piece", "kind": "find_move"}
    assert "solution_uci" not in repr(found)
