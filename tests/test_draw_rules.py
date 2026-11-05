"""A draw is declared only once it took place (#222, owner 05/11).

The third occurrence of a position on the board, or fifty real moves
without a capture nor a pawn move ; stalemate and insufficient material
as before. Never a draw the side to move could only *claim* by its next
move : ``can_claim_draw()`` drew a game for the opponent, before it
happened (a Tablier game ended ½-½ after a king move).
"""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl

#: The knights out and back twice : the start stands a 2nd time after 2...Ng8.
SHUFFLE = ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6", "Ng1"]
#: White rook and king against the king, 98 half-moves without capture nor pawn.
FIFTY = "8/8/8/4k3/8/8/8/R3K3 w - - 98 80"


def _state(tmp_path: Path, fen: str | None = None) -> GameState:
    meta = GameMetadata(
        game_id="draw", white="w", black="b", time_control=TimeControl(format="unrated")
    )
    return GameState.create(meta, games_dir=tmp_path, starting_fen=fen)


def test_a_repetition_only_claimable_ends_nothing(tmp_path: Path) -> None:
    state = _state(tmp_path)
    for san in SHUFFLE:
        last = state.play_move(san)
    # Black to move : ...Ng8 would be the 3rd occurrence, it is not played.
    assert state.board.can_claim_draw() and not state.board.is_repetition(3)
    assert state.metadata.result == "*"
    assert last["is_game_over"] is False and last["result"] == "*"
    assert GameState.load("draw", games_dir=tmp_path).metadata.result == "*"


def test_the_third_occurrence_on_the_board_is_a_draw(tmp_path: Path) -> None:
    state = _state(tmp_path)
    for san in SHUFFLE:
        state.play_move(san)
    last = state.play_move("Ng8")
    assert state.board.is_repetition(3)
    assert (state.metadata.result, state.metadata.termination) == ("1/2-1/2", "normal")
    # The arbiter reads is_game_over : a threefold is no python-chess game over.
    assert last["is_game_over"] is True and last["result"] == "1/2-1/2"


def test_fifty_moves_only_once_they_are_played(tmp_path: Path) -> None:
    state = _state(tmp_path, FIFTY)
    last = state.play_move("Ra2")  # the 99th half-move : 50 moves claimable, not played
    assert state.board.halfmove_clock == 99 and state.board.can_claim_fifty_moves()
    assert state.metadata.result == "*" and last["is_game_over"] is False
    last = state.play_move("Kd5")  # the 100th
    assert state.board.halfmove_clock == 100
    assert state.metadata.result == "1/2-1/2" and last["is_game_over"] is True


def test_a_mate_on_the_hundredth_half_move_is_a_mate(tmp_path: Path) -> None:
    # Black king on h8 ; Ra8# is the 100th half-move without capture nor pawn move.
    state = _state(tmp_path, "7k/8/6K1/8/8/8/8/R7 w - - 99 80")
    state.play_move("Ra8#")
    assert state.metadata.result == "1-0"


def test_a_capture_or_a_pawn_move_resets_the_count(tmp_path: Path) -> None:
    state = _state(tmp_path, "8/8/8/4k3/8/8/P7/R3K3 w - - 99 80")
    state.play_move("a3")
    assert state.board.halfmove_clock == 0 and state.metadata.result == "*"
