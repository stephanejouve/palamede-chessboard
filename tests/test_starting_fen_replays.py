"""Replays of ``san_history`` start from the game's root position (#36).

Every helper that replays the SAN list used to start from
``chess.Board()`` : wrong (or crashing) for a game created from
``starting_fen``. The engine guard case matters most — wrong
fingerprints let an in-progress exercise position be analysed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import chess

from palamede_chessboard._mcp_tool_handlers import _resolve_analyze_fen
from palamede_chessboard.chess_arbiter import move_request_context
from palamede_chessboard.chess_engine_guard import (
    _fingerprint_pair,
    _game_position_fingerprints,
    _position_fen_at_ply,
)
from palamede_chessboard.chessboard_engine import analyze_game
from palamede_chessboard.chessboard_state import GameMetadata, GameState
from tests.test_chessboard_engine import _mock_engine_with_scores

#: Black to move at move 20 : neither the colour nor the move number of
#: the standard start.
FEN = "4k3/8/8/8/8/8/4P3/R3K3 b - - 0 20"
MOVES = ["Kd7", "e4", "Kc6", "Ra6+"]


def _game(tmp_path: Path) -> GameState:
    state = GameState.create(
        GameMetadata(game_id="exo", white="w", black="b"),
        games_dir=tmp_path,
        starting_fen=FEN,
    )
    for san in MOVES:
        state.play_move(san)
    return GameState.load("exo", games_dir=tmp_path)


def _board_after(n: int) -> chess.Board:
    board = chess.Board(FEN)
    for san in MOVES[:n]:
        board.push_san(san)
    return board


def test_arbiter_last_moves_from_starting_fen(tmp_path: Path) -> None:
    board = _game(tmp_path).board
    ctx = move_request_context(board, last_n=3)
    assert ctx["last_moves"] == MOVES[-3:]
    assert ctx["fen"] == _board_after(4).fen()


def test_arbiter_no_moves_yet_from_starting_fen() -> None:
    # ply() is absolute (38 here) but nothing has been played.
    ctx = move_request_context(chess.Board(FEN))
    assert ctx["last_moves"] == []


def test_engine_guard_position_at_ply(tmp_path: Path) -> None:
    state = _game(tmp_path)
    assert _position_fen_at_ply(state, 0) == FEN
    assert _position_fen_at_ply(state, 2) == _board_after(2).fen()


def test_engine_guard_fingerprints_cover_exercise_positions(tmp_path: Path) -> None:
    fingerprints = _game_position_fingerprints(_game(tmp_path))
    assert _fingerprint_pair(_board_after(3).fen()) <= fingerprints


def test_analyze_position_ply_from_starting_fen(tmp_path: Path) -> None:
    _game(tmp_path)
    assert _resolve_analyze_fen("exo", None, 1, tmp_path) == _board_after(1).fen()


def test_analyze_game_walks_from_starting_fen(tmp_path: Path) -> None:
    state = _game(tmp_path)
    with patch(
        "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
        return_value=_mock_engine_with_scores([0] * 2 * len(MOVES)),
    ):
        walk = analyze_game(state, depth=8, engine_path="/fake")
    assert [e.san for e in walk] == MOVES
    assert [e.mover for e in walk] == ["black", "white", "black", "white"]
