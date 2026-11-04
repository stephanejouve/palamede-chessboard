"""The PGN ``Termination`` header says how a game ended (#151).

``normal`` for a game ended on the board, by resignation or agreement,
``time forfeit`` when a flag fell : nothing told them apart, and a loss on
time was lost once the game was over. The readable note (« admin
resigns ») stays in the comment before the result, where it already was ;
the games written before #151 held it in ``Termination`` and still read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.chessboard_clock import ClockState
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.clock_moves import _end_on_time
from palamede_chessboard.pgn_io import termination_from_pgn

#: 1.f3 e5 2.g4, black mates with Qh4#.
FOOL = ["f3", "e5", "g4", "Qh4#"]


def _state(tmp_path: Path, fen: str | None = None) -> GameState:
    meta = GameMetadata(
        game_id="term", white="w", black="b", time_control=TimeControl(format="unrated")
    )
    return GameState.create(meta, games_dir=tmp_path, starting_fen=fen)


def _termination(state: GameState) -> list[str]:
    return [line for line in state.raw_pgn().splitlines() if line.startswith("[Termination")]


def _legacy(tmp_path: Path, termination: str | None, tail: str) -> GameState:
    tags = ['[White "w"]', '[Black "b"]', '[Result "0-1"]']
    if termination is not None:
        tags.append(f'[Termination "{termination}"]')
    (tmp_path / "term.pgn").write_text("\n".join(tags) + f"\n\n1. e4 e5 {tail}\n", encoding="utf-8")
    return GameState.load("term", games_dir=tmp_path)


def test_a_game_in_progress_has_no_termination(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.play_move("e4")
    assert _termination(state) == []


def test_a_mate_is_a_normal_end(tmp_path: Path) -> None:
    state = _state(tmp_path)
    for san in FOOL:
        state.play_move(san)
    assert _termination(state) == ['[Termination "normal"]']
    assert state.raw_pgn().rstrip().endswith("Qh4# 0-1")


def test_a_draw_on_the_board_is_a_normal_end(tmp_path: Path) -> None:
    state = _state(tmp_path, fen="7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
    state.play_move("Qe6")  # stalemate
    assert state.metadata.result == "1/2-1/2"
    assert _termination(state) == ['[Termination "normal"]']


def test_a_resignation_is_a_normal_end_its_note_in_the_comment(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.play_move("e4")
    state.resign(by="black")
    pgn = state.raw_pgn()
    assert _termination(state) == ['[Termination "normal"]']
    assert "{b resigns} 1-0" in pgn
    loaded = GameState.load("term", games_dir=tmp_path)
    assert loaded.metadata.termination == "normal"
    assert loaded.metadata.termination_note == "b resigns"
    assert loaded.to_state_dict()["termination_note"] == "b resigns"
    loaded.save()
    assert loaded.raw_pgn() == pgn


def test_a_draw_agreed_is_a_normal_end(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.offer_draw(by="white")
    state.agree_draw(by="black")
    assert _termination(state) == ['[Termination "normal"]']
    loaded = GameState.load("term", games_dir=tmp_path)
    assert loaded.metadata.termination_note == "draw agreed"


@pytest.mark.parametrize(
    ("fen", "result"),
    [
        (None, "0-1"),
        # Black has a lone king : white's flag falling is a draw (FIDE 6.9).
        ("4k3/8/8/8/8/8/8/3QK3 w - - 0 1", "1/2-1/2"),
    ],
)
def test_a_flag_fallen_is_a_time_forfeit(tmp_path: Path, fen: str | None, result: str) -> None:
    state = _state(tmp_path, fen=fen)
    clock = ClockState(
        game_id="term",
        white_seconds=0,
        black_seconds=60,
        increment_seconds=0,
        flag_fall="white",
    )
    _end_on_time(state, clock)
    assert state.metadata.result == result
    assert _termination(state) == ['[Termination "time forfeit"]']
    loaded = GameState.load("term", games_dir=tmp_path)
    assert loaded.metadata.termination == "time forfeit"
    assert loaded.metadata.termination_note is None


def test_a_game_written_before_reads_its_note_from_the_header(tmp_path: Path) -> None:
    state = _legacy(tmp_path, "w resigns", "{w resigns} 0-1")
    assert state.metadata.termination == "normal"
    assert state.metadata.termination_note == "w resigns"
    state.save()
    assert _termination(state) == ['[Termination "normal"]']
    assert "{w resigns} 0-1" in state.raw_pgn()


def test_a_game_written_before_without_note_gains_no_termination(tmp_path: Path) -> None:
    state = _legacy(tmp_path, None, "0-1")
    assert state.metadata.termination is None
    state.save()
    assert _termination(state) == []


def test_an_imported_standard_termination_is_kept_as_written(tmp_path: Path) -> None:
    state = _legacy(tmp_path, "Time forfeit", "0-1")
    assert state.metadata.termination == "Time forfeit"
    assert state.metadata.termination_note is None
    state.save()
    assert _termination(state) == ['[Termination "Time forfeit"]']


@pytest.mark.parametrize(
    ("headers", "comment", "expected"),
    [
        ({}, "", (None, None)),
        ({}, "an annotation", (None, None)),
        ({"Termination": "normal"}, "", ("normal", None)),
        ({"Termination": "normal"}, "w resigns", ("normal", "w resigns")),
        ({"Termination": "time forfeit"}, "", ("time forfeit", None)),
        ({"Termination": "draw agreed"}, "draw agreed", ("normal", "draw agreed")),
    ],
)
def test_termination_from_pgn(headers: dict, comment: str, expected: tuple) -> None:
    assert termination_from_pgn(headers, comment) == expected
