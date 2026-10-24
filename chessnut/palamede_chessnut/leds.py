"""LED signals that are not guidance : short animations, one frame per tick.

Guidance (the squares to act on) always wins : the bridge drops an
animation as soon as a square has to light up. These frames only fill
the moments when the board is otherwise dark.
"""

from __future__ import annotations

import chess

Frame = frozenset[chess.Square]

#: Tick of the bridge loop ; an animation step lasts one tick.
_HOLD = 1


def _rank(rank: int) -> Frame:
    return frozenset(chess.square(file, rank) for file in range(8))


def ready_sweep() -> list[Frame]:
    """The board matches the game : one line runs from rank 1 to rank 8, then dark."""
    return [_rank(rank) for rank in range(8) for _ in range(_HOLD)] + [frozenset()]


def finished_blink() -> list[Frame]:
    """The game is over : the whole board blinks twice, then dark."""
    full: Frame = frozenset(chess.SQUARES)
    dark: Frame = frozenset()
    return [full] * 3 + [dark] * 3 + [full] * 3 + [dark]


def countdown(square: chess.Square | None, elapsed: float) -> Frame:
    """Resignation pending : the king's square blinks twice a second."""
    if square is None or int(elapsed * 4) % 2:
        return frozenset()
    return frozenset({square})
