"""``starting_fen`` persistence (issue #36).

Before the fix, ``GameState.save`` wrote neither ``[SetUp "1"]`` nor
``[FEN "…"]`` : ``load`` (``game.board()``) restarted from the initial
position, so an exercise was lost on the first MCP call after creation.
"""

from __future__ import annotations

from pathlib import Path

import chess

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl

#: King and pawn endgame, white to move.
WHITE_FEN = "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"
#: Same material, black to move, move 12.
BLACK_FEN = "8/8/8/4k3/8/8/4P3/4K3 b - - 0 12"


def _meta(game_id: str) -> GameMetadata:
    return GameMetadata(
        game_id=game_id,
        white="stephane",
        black="claude-desktop",
        time_control=TimeControl(format="unrated"),
        rated=False,
    )


def _pgn(games_dir: Path, game_id: str) -> str:
    return (games_dir / f"{game_id}.pgn").read_text(encoding="utf-8")


def test_standard_game_writes_no_setup_and_same_movetext(tmp_path: Path) -> None:
    """Non-regression : a standard game keeps its exact previous PGN form."""
    state = GameState.create(_meta("std"), games_dir=tmp_path)
    for san in ("e4", "e5", "Nf3"):
        state.play_move(san)
    text = _pgn(tmp_path, "std")
    assert "[SetUp " not in text and "[FEN " not in text
    assert "1. e4 e5 2. Nf3 *" in text


def test_starting_fen_survives_reload(tmp_path: Path) -> None:
    GameState.create(_meta("exo"), games_dir=tmp_path, starting_fen=WHITE_FEN)
    text = _pgn(tmp_path, "exo")
    assert '[SetUp "1"]' in text
    assert f'[FEN "{WHITE_FEN}"]' in text
    assert GameState.load("exo", games_dir=tmp_path).board.fen() == WHITE_FEN


def test_move_from_fen_replays_after_reload(tmp_path: Path) -> None:
    state = GameState.create(_meta("exo"), games_dir=tmp_path, starting_fen=WHITE_FEN)
    state.play_move("Kd2")
    reloaded = GameState.load("exo", games_dir=tmp_path)
    expected = chess.Board(WHITE_FEN)
    expected.push_san("Kd2")
    assert reloaded.board.fen() == expected.fen()
    assert reloaded.san_history == ["Kd2"]
    reloaded.play_move("Kd5")  # the game goes on from the exercise position
    assert GameState.load("exo", games_dir=tmp_path).san_history == ["Kd2", "Kd5"]


def test_black_to_move_numbering(tmp_path: Path) -> None:
    state = GameState.create(_meta("exo-b"), games_dir=tmp_path, starting_fen=BLACK_FEN)
    state.play_move("Kd5")
    state.play_move("Kd2")
    assert "12... Kd5 13. Kd2 *" in _pgn(tmp_path, "exo-b")
    reloaded = GameState.load("exo-b", games_dir=tmp_path)
    assert reloaded.san_history == ["Kd5", "Kd2"]
    assert reloaded.board.fullmove_number == 13
