"""The time left after each move is written as ``[%clk]`` (#151).

The PGN kept no clock : neither the curve of the clocks in analysis nor,
after the game, who was short of time. When the game has a clock, each
move is now followed by the time left to the side that played it, as in
the PGN supplement : ``1. d4 { [%clk 0:15:00] } 1... d5 { [%clk 0:14:58] }``.
The last move's ``[%clk]`` shares its comment with the termination note,
which is read without it, from our games and from the imports alike.
"""

from __future__ import annotations

from pathlib import Path

import chess
import pytest

from palamede_chessboard import chessboard_clock
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.pgn_io import _clk, _movetext, termination_from_pgn
from tests.mcp.conftest import _decode

LICHESS = """[Event "Rated Blitz game"]
[White "a"]
[Black "b"]
[Result "1-0"]
[TimeControl "180+2"]
[Termination "Normal"]

1. e4 { [%clk 0:03:00] } 1... e5 { [%clk 0:02:59.5] } 2. Qh5 { [%clk 0:02:58] } \
2... Nc6 { [%clk 0:02:57] } 3. Bc4 { [%clk 0:02:55] } 3... Nf6 { [%clk 0:02:50] } \
4. Qxf7# { [%clk 0:02:54] } 1-0
"""


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (900, "[%clk 0:15:00]"),
        (898.7, "[%clk 0:14:58]"),
        (3725, "[%clk 1:02:05]"),
        (-3, "[%clk 0:00:00]"),
    ],
)
def test_clk_format(seconds: float, text: str) -> None:
    assert _clk(seconds) == text


def test_the_movetext_of_the_issue() -> None:
    text = _movetext(["d4", "d5", "c4"], chess.Board(), [900, 898, None])
    assert text == "1. d4 { [%clk 0:15:00] } 1... d5 { [%clk 0:14:58] } 2. c4"


def test_only_a_comment_numbers_the_black_move_after_it() -> None:
    # A clock that stops (or starts late) : the numbering follows each comment.
    text = _movetext(["d4", "d5", "c4", "c5"], chess.Board(), [900, None, None, None])
    assert text == "1. d4 { [%clk 0:15:00] } 1... d5 2. c4 c5"


def test_no_clock_no_comment() -> None:
    assert _movetext(["d4", "d5", "c4"], chess.Board(), [None, None, None]) == "1. d4 d5 2. c4"
    assert _movetext(["d4", "d5"], chess.Board()) == "1. d4 d5"


def _game(tmp_path: Path, moves: list[str], clocks: list[float | None]) -> GameState:
    meta = GameMetadata(
        game_id="clk", white="w", black="b", time_control=TimeControl(format="15+0")
    )
    state = GameState.create(meta, games_dir=tmp_path)
    for san in moves:
        state.play_move(san)
    state.clocks = list(clocks)
    state.save()
    return state


def test_the_clocks_survive_save_and_load(tmp_path: Path) -> None:
    state = _game(tmp_path, ["e4", "e5", "Nf3"], [900, 897, 880])
    first = state.raw_pgn()
    loaded = GameState.load("clk", games_dir=tmp_path)
    assert loaded.clocks == [900, 897, 880]
    loaded.save()
    assert loaded.raw_pgn() == first


def test_the_note_after_the_last_clock_is_read_without_it(tmp_path: Path) -> None:
    state = _game(tmp_path, ["e4", "e5"], [900, 897])
    state.resign(by="white")
    assert "1... e5 { [%clk 0:14:57] } {w resigns} 0-1" in state.raw_pgn()
    loaded = GameState.load("clk", games_dir=tmp_path)
    assert loaded.metadata.termination_note == "w resigns"
    assert loaded.clocks == [900, 897]


def test_a_lichess_import_keeps_its_clocks_and_has_no_note(tmp_path: Path) -> None:
    (tmp_path / "clk.pgn").write_text(LICHESS, encoding="utf-8")
    state = GameState.load("clk", games_dir=tmp_path)
    assert state.metadata.termination == "Normal"
    assert state.metadata.termination_note is None
    assert state.clocks == [180, 179.5, 178, 177, 175, 170, 174]
    state.save()
    assert "4. Qxf7# { [%clk 0:02:54] } 1-0" in state.raw_pgn()


@pytest.mark.parametrize(
    ("comment", "note"),
    [
        ("[%clk 0:00:10]", None),
        ("[%clk 0:00:10] w resigns", "w resigns"),
        ("[%clk 0:00:10] [%eval 0.3] draw agreed", "draw agreed"),
    ],
)
def test_the_commands_are_not_the_note(comment: str, note: str | None) -> None:
    assert termination_from_pgn({"Termination": "normal"}, comment) == ("normal", note)


@pytest.mark.asyncio
async def test_a_game_played_with_its_clock_writes_the_time_left(
    games_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(chessboard_clock, "_now", lambda: now[0])
    await call_tool(
        "chess_create_game",
        {"game_id": "clk", "white": "w", "black": "b", "time_control": {"format": "5+3"}},
    )
    await call_tool("chess_clock_start", {"game_id": "clk"})
    now[0] = 1010.0
    white = _decode(await call_tool("chess_play_move", {"game_id": "clk", "move_san": "e4"}))
    now[0] = 1015.0
    black = _decode(await call_tool("chess_play_move", {"game_id": "clk", "move_san": "e5"}))
    pgn = _decode(await call_tool("chess_get_raw_pgn", {"game_id": "clk"}))["pgn"]
    white_left = white["clock"]["white_seconds"]
    black_left = black["clock"]["black_seconds"]
    assert (white_left, black_left) == (296, 301)  # 303 - 10 + 3, 303 - 5 + 3
    assert "1. e4 { [%clk 0:04:56] } 1... e5 { [%clk 0:05:01] } *" in pgn


@pytest.mark.asyncio
async def test_a_game_without_clock_writes_none(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "noclk", "white": "w", "black": "b"})
    await call_tool("chess_play_move", {"game_id": "noclk", "move_san": "e4"})
    pgn = _decode(await call_tool("chess_get_raw_pgn", {"game_id": "noclk"}))["pgn"]
    assert pgn.rstrip().endswith("1. e4 *")
