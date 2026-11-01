"""Read moves off a physical board : position comparison, not sensor events.

The board reports occupancy and piece type for every square, so a move is
the legal move whose resulting placement equals the one observed. This
covers captures, castling, en passant and promotion (the piece actually
put down decides the promotion) without tracking lifts and drops : the
intermediate placements of a move in progress match no legal move.

A move whose result happens to match while the player is still adjusting
(a knight dropped on a3 on its way to c3) is filtered by the caller, which
only accepts a placement that stayed unchanged for a short delay.
"""

from __future__ import annotations

import chess


def find_move(board: chess.Board, observed: chess.BaseBoard) -> chess.Move | None:
    """Legal move of ``board`` leading to ``observed``, or ``None``."""
    target = observed.board_fen()
    for move in board.legal_moves:
        board.push(move)
        reached = board.board_fen()
        board.pop()
        if reached == target:
            return move
    return None


def differing_squares(expected: chess.BaseBoard, observed: chess.BaseBoard) -> set[chess.Square]:
    """Squares whose content differs, i.e. where the player has to act."""
    return {sq for sq in chess.SQUARES if expected.piece_at(sq) != observed.piece_at(sq)}


def relayed(before: chess.Board | None, san: str, after: chess.Board) -> str:
    """``g8f6 (Nf6)`` ; the SAN alone when ``before`` is not the position it was played from."""
    if before is not None:
        try:
            move = before.parse_san(san)
        except ValueError:
            return san
        board = before.copy(stack=False)
        board.push(move)
        if board.board_fen() == after.board_fen():
            return f"{move.uci()} ({san})"
    return san
