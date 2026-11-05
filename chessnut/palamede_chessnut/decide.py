"""What the bridge does with a still placement : the LEDs, and the move to post.

Out of :mod:`.bridge`, which keeps the links (board, stream, viewer), so
that a change of the rules does not grow it past its budget.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

from palamede_chessnut import leds
from palamede_chessnut.moves import differing_squares, find_move

SETTLE_SECONDS = 0.6
GUIDE_SECONDS = 2.0


@dataclass(frozen=True)
class Decision:
    """LEDs to show, and the move to post if the player just played one."""

    leds: frozenset[chess.Square]
    move: chess.Move | None = None


def decide(
    game: chess.Board,
    player: chess.Color,
    observed: chess.BaseBoard,
    settled_for: float,
    *,
    live: bool = True,
    synced: bool = True,
    pending: bool = False,
    refused: chess.Move | None = None,
) -> Decision | None:
    """What to do with a placement still for ``settled_for`` seconds (``None`` : wait).

    ``synced`` : the board has matched the game since its last change ;
    until then the squares to fix light up without delay, and no move is
    read : a placement that happens to equal « position + one legal move »
    while the pieces are being set up is not a move.
    ``pending`` : a move was posted and the stream has not confirmed it yet.
    ``refused`` : the viewer rejected that move from this position ; it is
    not posted again, and its squares blink until it is taken back : steady,
    they would read as the opponent's move to replicate (Stéphane, 05/11).
    """
    if settled_for < SETTLE_SECONDS:
        return None
    expected = chess.BaseBoard(game.board_fen())
    if observed.board_fen() == expected.board_fen() or pending:
        return Decision(frozenset())
    if live and synced and game.turn == player:
        move = find_move(game, observed)
        if move is not None and move == refused:
            return Decision(
                leds.blink(frozenset(differing_squares(expected, observed)), settled_for)
            )
        if move is not None:
            return Decision(frozenset(), move)
        if move is None and settled_for < GUIDE_SECONDS:
            return Decision(frozenset())
    return Decision(frozenset(differing_squares(expected, observed)))
