"""Why a move was a mistake (#95) : its motif, read on the board.

The engine says how bad a move was ; the motif says what kind of
mistake it was, so a lesson can be built on it. The first that applies,
in this order :

1. ``missed_mate`` — the mover had a forced mate and lost it (#100).
2. ``hanging_piece`` — the move leaves one of the mover's pieces en prise.
3. ``ignored_threat`` — a piece of the mover was already en prise, the
   move did not move it, and it still is.
4. ``lone_queen`` — the queen goes out early, before two minor pieces
   are out. *Heuristic* ; an attacked queen is already ``hanging_piece``.
5. ``weakened_king`` — a pawn of the castled king's shelter moves, or
   the king walks and loses castling early. *Heuristic.*
6. ``other``.

The two heuristics are in the report, marked as such, but give no
exercise until they are validated (Leader, #95). « En prise » : the
opponent wins material by the exchange on the square, attackers and
defenders counted (#303). Stricter for a beginner than the ``hanging`` of
``chess_board_facts``, which calls a piece defended once safe.
"""

from __future__ import annotations

import chess

from palamede_chessboard._mcp_shared import _PIECE_VALUES
from palamede_chessboard.chessboard_engine import GameWalkEntry

MOTIFS = (
    "missed_mate",
    "hanging_piece",
    "ignored_threat",
    "lone_queen",
    "weakened_king",
    "other",
)
#: Motifs not yet validated : reported, but no exercise is drawn from them.
HEURISTIC_MOTIFS = frozenset({"lone_queen", "weakened_king"})

#: Before this move number, a queen out alone is « early ».
EARLY_QUEEN_MOVES = 10
#: Before this move number, a king that walks loses castling « early ».
EARLY_KING_MOVES = 12

_MINOR_HOMES = {
    chess.WHITE: (chess.B1, chess.C1, chess.F1, chess.G1),
    chess.BLACK: (chess.B8, chess.C8, chess.F8, chess.G8),
}


def _next_capturer(board: chess.Board, side: chess.Color, square: int) -> int | None:
    """The cheapest piece of ``side`` able to capture on ``square``, the king last.

    A pinned piece captures only along its pin ; the king only where nothing
    would take it back.
    """
    able = [a for a in board.attackers(side, square) if square in board.pin(side, a)]
    pieces = sorted(
        (a for a in able if a != board.king(side)),
        key=lambda a: _PIECE_VALUES[board.piece_type_at(a)],  # type: ignore[index]
    )
    if pieces:
        return pieces[0]
    king = board.king(side)
    if king in able and not board.attackers(not side, square):
        return king
    return None


def _exchange_gain(board: chess.Board, side: chess.Color, square: int, target: int) -> int:
    """What ``side`` wins by capturing first on ``square``, free to stop at any time.

    ``board`` is consumed : each capturer leaves its square, which lets the
    pieces lined up behind it join the exchange.
    """
    capturer = _next_capturer(board, side, square)
    if capturer is None:
        return 0
    value = _PIECE_VALUES[board.piece_type_at(capturer)]  # type: ignore[index]
    board.remove_piece_at(capturer)
    return max(0, target - _exchange_gain(board, not side, square, value))


def _hanging(board: chess.Board, color: str) -> set[str]:
    """Squares of ``color``'s pieces the opponent wins material by capturing (#303)."""
    mine = color == "white"
    return {
        chess.square_name(square)
        for square in chess.SquareSet(board.occupied_co[mine])
        if _exchange_gain(
            board.copy(stack=False),
            not mine,
            square,
            _PIECE_VALUES[board.piece_type_at(square)],  # type: ignore[index]
        )
        > 0
    }


def _lone_queen(before: chess.Board, move: chess.Move) -> bool:
    piece = before.piece_at(move.from_square)
    if piece is None or piece.piece_type != chess.QUEEN:
        return False
    if before.fullmove_number >= EARLY_QUEEN_MOVES:
        return False
    color = piece.color
    homes = _MINOR_HOMES[color]
    developed = sum(
        1
        for square in homes
        if not (
            (p := before.piece_at(square)) is not None
            and p.color == color
            and p.piece_type in (chess.KNIGHT, chess.BISHOP)
        )
    )
    return developed < 2


def _shelter_files(king_square: int) -> tuple[int, ...] | None:
    """The files of the pawn shelter of a castled king, else ``None``."""
    file = chess.square_file(king_square)
    if file >= 6:  # g or h : castled short
        return (5, 6, 7)
    if file <= 2:  # a, b or c : castled long
        return (0, 1, 2)
    return None


def _weakened_king(before: chess.Board, move: chess.Move) -> bool:
    piece = before.piece_at(move.from_square)
    if piece is None:
        return False
    color = piece.color
    if piece.piece_type == chess.KING:
        return (
            before.has_castling_rights(color)
            and not before.is_castling(move)
            and before.fullmove_number < EARLY_KING_MOVES
        )
    if piece.piece_type != chess.PAWN:
        return False
    king = before.king(color)
    if king is None or chess.square_rank(king) != (0 if color == chess.WHITE else 7):
        return False
    files = _shelter_files(king)
    return files is not None and chess.square_file(move.from_square) in files


def motif_of(entry: GameWalkEntry) -> str:
    """The motif of ``entry``, a move of the game walk (see the module doc)."""
    if entry.missed_mate:
        return "missed_mate"
    before = chess.Board(entry.fen_before)
    move = before.parse_san(entry.san)
    after = before.copy()
    after.push(move)
    me = entry.mover
    hanging_before = _hanging(before, me)
    hanging_after = _hanging(after, me)
    if hanging_after - hanging_before:
        return "hanging_piece"
    # The moved piece left its square : what is still en prise stayed.
    if hanging_before & hanging_after:
        return "ignored_threat"
    if _lone_queen(before, move):
        return "lone_queen"
    if _weakened_king(before, move):
        return "weakened_king"
    return "other"
