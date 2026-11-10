"""What a player reads when the viewer refuses a move or an action (#322).

French, French notation (R, D, T, F, C), no UCI, no ply, no internal code :
the technical detail goes to the log, the code to the ``X-Refusal-Code``
header for programs and tests. The Tablier's bridge shows ``detail`` as is
when it starts with « Coup refusé » (:mod:`palamede_chessnut.refusal`).
"""

from __future__ import annotations

import chess

#: English SAN piece letters → French ones. SAN writes pieces in upper case
#: only (files are lower case), so a plain translation is exact.
_FRENCH_LETTERS = str.maketrans("KQRBN", "RDTFC")
_FRENCH_PIECE = {
    chess.KING: "R",
    chess.QUEEN: "D",
    chess.ROOK: "T",
    chess.BISHOP: "F",
    chess.KNIGHT: "C",
    chess.PAWN: "",
}

UNREADABLE = "Coup refusé : coup illisible."
NO_MOVE = "Coup refusé : aucun coup reçu."
GAME_OVER = "Coup refusé : la partie est terminée."


def french_san(san: str) -> str:
    """``Rf1`` → ``Tf1``, ``exd8=Q+`` → ``exd8=D+`` ; castling unchanged."""
    return san.translate(_FRENCH_LETTERS)


def attempted(board: chess.Board, move: chess.Move) -> str:
    """The move a player tried, said the French way even when illegal.

    The piece on the start square and the target square (``Tf1``, ``e5``) ;
    no piece there : « aucune pièce en h1 ».
    """
    piece = board.piece_at(move.from_square)
    if piece is None:
        return f"aucune pièce en {chess.square_name(move.from_square)}"
    return _FRENCH_PIECE[piece.piece_type] + chess.square_name(move.to_square)


def not_possible(what: str) -> str:
    """« Coup refusé : Tf1 n'est pas possible dans cette position. »"""
    if what.startswith("aucune pièce"):
        return f"Coup refusé : {what}."
    return f"Coup refusé : {what} n'est pas possible dans cette position."


def is_over(board: chess.Board, result: str) -> bool:
    return result != "*" or board.is_game_over()


def end_refused(action: str, board: chess.Board, result: str, offer: dict | None, side: str) -> str:
    """Why a resignation or a draw action was refused, from the game itself.

    ``action`` : ``resign``, ``offer``, ``agree`` or ``decline`` ; ``side`` :
    the side of the player who asked. Read from the state, never from the
    English text of the state machine.
    """
    if is_over(board, result):
        return "La partie est terminée."
    if action == "offer":
        return "Votre adversaire propose déjà la nulle : acceptez-la ou refusez-la."
    if offer is None:
        return "Aucune proposition de nulle en cours."
    if offer.get("by") == side:
        return "C'est votre propre proposition : votre adversaire doit répondre."
    return "Action refusée dans l'état actuel de la partie."
