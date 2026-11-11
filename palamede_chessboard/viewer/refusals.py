"""What a player reads when the viewer refuses a move or an action (#322).

The texts are the domain's (:mod:`palamede_chessboard.refusals`, #332, #340) :
one operation, the same refusals on every medium. This module adds only what
the viewer reads from the game itself. The Tablier's bridge shows ``detail``
as is when it starts with « Coup refusé » (:mod:`palamede_chessnut.refusal`).
"""

from __future__ import annotations

import chess

from palamede_chessboard.refusals import (
    END_GAME_OVER,
    END_NO_OFFER,
    END_OPPONENT_OFFERED,
    END_OWN_OFFER,
    END_REFUSED,
    GAME_OVER,
    MOVE_REFUSALS,
    NO_MOVE,
    UNREADABLE,
    attempted,
    french_san,
    not_possible,
)

__all__ = [
    "GAME_OVER",
    "MOVE_REFUSALS",
    "NO_MOVE",
    "UNREADABLE",
    "attempted",
    "end_refused",
    "french_san",
    "is_over",
    "not_possible",
]


def is_over(board: chess.Board, result: str) -> bool:
    return result != "*" or board.is_game_over()


def end_refused(action: str, board: chess.Board, result: str, offer: dict | None, side: str) -> str:
    """Why a resignation or a draw action was refused, from the game itself.

    ``action`` : ``resign``, ``offer``, ``agree`` or ``decline`` ; ``side`` :
    the side of the player who asked. Read from the state, never from the
    English text of the state machine.
    """
    if is_over(board, result):
        return END_GAME_OVER
    if action == "offer":
        return END_OPPONENT_OFFERED
    if offer is None:
        return END_NO_OFFER
    if offer.get("by") == side:
        return END_OWN_OFFER
    return END_REFUSED
