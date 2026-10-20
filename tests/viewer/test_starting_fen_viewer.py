"""Viewer on a game created from ``starting_fen`` (#36 follow-up).

The viewer replayed ``san_history`` from ``chess.Board()`` : an exercise
created from a FEN showed the standard start position, and the first
move could not even be replayed.
"""

from __future__ import annotations

from pathlib import Path

import chess
import chess.svg
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl

#: Position of the exercise where the bug was reported (White mates with Re8#).
EXO_FEN = "6k1/5ppp/8/8/8/8/2r2PPP/4R1K1 w - - 0 1"
#: Black to move, at move 20 : both the colour and the number differ
#: from the standard start.
BLACK_FEN = "4k3/8/8/8/8/8/4P3/R3K3 b - - 0 20"


def _exercise(games_dir: Path, game_id: str, fen: str) -> GameState:
    return GameState.create(
        GameMetadata(
            game_id=game_id,
            white="human",
            black="claude-desktop",
            time_control=TimeControl(format="unrated"),
        ),
        games_dir=games_dir,
        starting_fen=fen,
    )


def test_positions_start_from_starting_fen(client: TestClient, games_dir: Path) -> None:
    _exercise(games_dir, "exo", EXO_FEN)
    body = client.get("/api/positions?game_id=exo").json()
    assert body["positions"] == [
        {"fen": EXO_FEN, "san": None, "from": None, "to": None, "color": None}
    ]


def test_positions_replay_moves_from_starting_fen(client: TestClient, games_dir: Path) -> None:
    _exercise(games_dir, "exo", EXO_FEN).play_move("Re8#")
    positions = client.get("/api/positions?game_id=exo").json()["positions"]
    expected = chess.Board(EXO_FEN)
    expected.push_san("Re8#")
    assert positions[1] == {
        "fen": expected.fen(),
        "san": "Re8#",
        "from": "e1",
        "to": "e8",
        "color": "w",
    }


def test_positions_black_to_move_at_move_20(client: TestClient, games_dir: Path) -> None:
    state = _exercise(games_dir, "exo-b", BLACK_FEN)
    state.play_move("Kd7")
    state.play_move("e4")
    positions = client.get("/api/positions?game_id=exo-b").json()["positions"]
    assert positions[0]["fen"] == BLACK_FEN
    assert [p["color"] for p in positions] == [None, "b", "w"]
    # The template numbers moves from these FENs : 20... Kd7 21. e4
    assert [p["fen"].split()[5] for p in positions] == ["20", "21", "21"]


def test_board_svg_draws_starting_fen(client: TestClient, games_dir: Path) -> None:
    state = _exercise(games_dir, "exo-b", BLACK_FEN)
    state.play_move("Kd7")
    root = chess.Board(BLACK_FEN)
    r0 = client.get("/api/board.svg?game_id=exo-b&ply=0")
    assert r0.status_code == 200
    assert r0.text == chess.svg.board(board=root, size=400, lastmove=None)
    move = root.parse_san("Kd7")
    root.push(move)
    r1 = client.get("/api/board.svg?game_id=exo-b&ply=1")
    assert r1.text == chess.svg.board(board=root, size=400, lastmove=move)
