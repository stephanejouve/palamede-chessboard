"""« Cases attaquées » answered on the board (#237, step 3, owner 05/11 to 09/11).

A piece of the player lifted then put back designates its square, which
lights up ; the same gesture takes it back. The king lifted and put back
twice sends the set, even an empty one. Only the board's whole position
counts : any other difference (two pieces up, a piece moved) designates
nothing until the board matches the game again.

No beep while designating : two high ones for a right answer, two low ones
for a wrong one. The Air+ has no colour to command (:mod:`.sounds`) : green is
the window's (owner, 09/11), the board lights its own.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import chess

from palamede_chessnut import exercise, leds
from palamede_chessnut.moves import differing_squares

if TYPE_CHECKING:
    from palamede_chessnut.bridge import Bridge

log = logging.getLogger(__name__)

#: Longer in the air, the king is no validation : the resignation may count (owner, 09/11).
KING_HOLD_SECONDS = 2.0
#: Shorter, a flicker of the sensors rather than a hand.
MIN_LIFT_SECONDS = 0.2
NO_SERVER = "Le serveur n'a pas répondu : validez à nouveau."


class Designation:
    """The squares designated so far, and the gesture under way."""

    def __init__(self) -> None:
        self.squares: set[chess.Square] = set()
        #: What the window says of the last answer, until the next gesture.
        self.note: str | None = None
        self._lifted: chess.Square | None = None
        self._since = 0.0
        self._armed = True
        self._taps = 0
        self._last_tap = 0.0

    def names(self) -> list[str]:
        return sorted(chess.square_name(square) for square in self.squares)

    def feed(
        self, expected: chess.BaseBoard, observed: chess.BaseBoard, player: chess.Color, now: float
    ) -> bool:
        """Follow the board ; ``True`` when the king has just validated."""
        differing = differing_squares(expected, observed)
        if not differing:
            return self._put_back(expected, player, now)
        square = next(iter(differing))
        piece = expected.piece_at(square)
        if (
            len(differing) > 1
            or observed.piece_at(square) is not None
            or piece is None
            or piece.color != player
            or self._lifted not in (None, square)
        ):
            self._lifted, self._armed, self._taps = None, False, 0
        elif self._lifted is None:
            if now - self._last_tap > KING_HOLD_SECONDS:  # from put back to next lift only
                self._taps = 0
            self._lifted, self._since = square, now
        return False

    def _put_back(self, expected: chess.BaseBoard, player: chess.Color, now: float) -> bool:
        lifted, held, armed = self._lifted, now - self._since, self._armed
        self._lifted, self._armed = None, True
        if lifted is None or not armed or held < MIN_LIFT_SECONDS:
            return False
        if lifted != expected.king(player):
            self._taps, self.note = 0, None
            self.squares ^= {lifted}
            return False
        if held >= KING_HOLD_SECONDS:
            self._taps = 0
            return False
        self._taps, self._last_tap = self._taps + 1, now
        if self._taps < 2:
            return False
        self._taps = 0
        return True


def _answers_on_board(bridge: Bridge) -> bool:
    """An « attacked » exercise whose position stands, the player to move."""
    return (
        bridge.ready
        and bridge.exercise is not None
        and bridge.exercise["kind"] == "attacked"
        and bool(bridge.exercise["id"])
        and bridge.game is not None
        and bridge.game.turn == bridge.player
    )


async def follow(bridge: Bridge, now: float) -> None:
    """One tick : read the gesture, and send the answer once it is validated."""
    if not _answers_on_board(bridge):
        return
    assert bridge.game is not None and bridge.observed is not None
    expected = chess.BaseBoard(bridge.game.board_fen())
    if bridge.designation.feed(expected, bridge.observed, bridge.player, now):
        await _submit(bridge)


def _detail(body: Any) -> str:
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, str) and detail else "Réponse refusée"


async def _submit(bridge: Bridge) -> None:
    designation = bridge.designation
    squares = designation.names()
    log.info("answer on the board : %s", " ".join(squares) or "no square")
    status, body = await asyncio.to_thread(exercise.answer, bridge, squares)
    if status is not None and 200 <= status < 300:
        designation.note = None
        bridge.animation = leds.chosen(frozenset(designation.squares)) if squares else []
        designation.squares.clear()
        bridge.say("ok")
    elif status is not None and 400 <= status < 500:
        designation.note = f"{_detail(body)}. Corrigez puis validez à nouveau."
        bridge.say("no")
    else:
        log.warning("answer not delivered (HTTP %s)", status)
        designation.note = NO_SERVER
