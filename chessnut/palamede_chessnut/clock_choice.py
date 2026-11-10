"""The white knights choose the clock of the games opened next (#186).

From the starting position, either knight put on rank 3 says the base
time, on rank 4 the increment, the file saying the value (a3 3 min … f3
90 min, a4 0 s … f4 30 s). The lobby (:mod:`.lobby`) holds the gesture
and takes the choice ; here what a placement says, the clock chosen, and
the values the screen's board shows on the squares.
"""

from __future__ import annotations

import chess

from palamede_chessnut import config

#: The knight still on its square that long : a choice, not a square crossed (owner, 2 to 3 s).
CHOICE_SECONDS = 2.0
#: Part of the clock (0 the base minutes, 1 the increment seconds) → the rank
#: a white knight chooses it on, and the values from the a-file on (owner
#: 02/11, 04/11) ; either knight, both may be off at once (smoke of Stéphane, 04/11).
CHOICES = {
    0: (2, (3, 5, 10, 15, 25, 90)),
    1: (3, (0, 2, 3, 5, 10, 30)),
}
KNIGHT_HOMES = (chess.B1, chess.G1)
#: A knight's choice : ``(part, value, square)``.
Choice = tuple[int, int, chess.Square]


def without(squares: tuple[chess.Square, ...]) -> chess.BaseBoard:
    """The starting position without the pieces of ``squares``."""
    start = chess.BaseBoard()
    for square in squares:
        start.remove_piece_at(square)
    return start


def knights_off(observed: chess.BaseBoard) -> list[chess.Square] | None:
    """The starting position but for its white knights : the squares of those off b1 and g1.

    Empty while they are home or lifted ; ``None`` for any other change,
    a spare knight included (one more knight than homes left).
    """
    knight = chess.Piece(chess.KNIGHT, chess.WHITE)
    rest = without(KNIGHT_HOMES)
    off = []
    for square in chess.SQUARES:
        piece = observed.piece_at(square)
        if piece == rest.piece_at(square) or (square in KNIGHT_HOMES and piece == knight):
            continue
        if piece != knight or rest.piece_at(square) is not None:
            return None
        off.append(square)
    left = sum(observed.piece_at(home) != knight for home in KNIGHT_HOMES)
    return off if len(off) <= left else None


def choices(observed: chess.BaseBoard) -> tuple[Choice, ...]:
    """The clock's parts and values the knights' squares say (g3, h3, the other ranks say nothing)."""
    found = []
    for square in knights_off(observed) or ():
        for part, (rank, values) in CHOICES.items():
            file = chess.square_file(square)
            if chess.square_rank(square) == rank and file < len(values):
                found.append((part, values[file], square))
    return tuple(found)


def chosen(time_control: str, picked: tuple[Choice, ...]) -> str:
    """``time_control`` with each part picked set to its value ; without a clock, from the default one."""
    if time_control == "unrated":
        time_control = config.NEW_GAME["time_control"]
    clock = time_control.split("+")
    for part, value, _square in picked:
        clock[part] = str(value)
    return "+".join(clock)


def tips() -> dict[str, str]:
    """Square → the value the knight chooses there, for the screen's board (smoke of Stéphane, 04/11)."""
    unit = {0: "′", 1: ""}
    sign = {0: "", 1: "+"}
    return {
        chess.square_name(chess.square(file, rank)): f"{sign[part]}{value}{unit[part]}"
        for part, (rank, values) in CHOICES.items()
        for file, value in enumerate(values)
    }


def said(time_control: str) -> str:
    """The clock as the window says it."""
    return "sans pendule" if time_control == "unrated" else time_control
