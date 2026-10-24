"""Chessnut Air+ wire format, as observed on the device (2026-10-23).

GATT characteristics (all in the ``1b7e82xx-2877-41c3-b46e-cf057c562023``
family) :

* ``…8262`` (notify) — board frames, about ten per second once enabled ;
* ``…8272`` (write) — commands : :data:`ENABLE_REPORTING`, LED frames ;
* ``…8273`` (notify) — command acknowledgements (``23 01 00`` on connect).

A board frame is ``01 24`` followed by 32 placement bytes, then a one-byte
counter (about 1 Hz) and three zero bytes. Placement byte ``i`` holds two
squares of rank ``i // 4`` (rank 1 first) : the low nibble is file
``2 * (i % 4)``, the high nibble the next file. Nibble codes are
:data:`PIECE_CODES` ; 0 is an empty square.

An LED frame is ``0a 08`` followed by eight bytes, one per rank (rank 1
first), bit ``1 << file`` lighting that square (``0x01`` = file a).
"""

from __future__ import annotations

from collections.abc import Iterable

import chess

BOARD_UUID = "1b7e8262-2877-41c3-b46e-cf057c562023"
COMMAND_UUID = "1b7e8272-2877-41c3-b46e-cf057c562023"
ACK_UUID = "1b7e8273-2877-41c3-b46e-cf057c562023"

#: Starts the stream of board frames on :data:`BOARD_UUID`.
ENABLE_REPORTING = bytes([0x21, 0x01, 0x00])

BOARD_HEADER = bytes([0x01, 0x24])
LED_HEADER = bytes([0x0A, 0x08])

#: Nibble → piece symbol (upper case = white). All twelve checked on the
#: start position of an Air+.
PIECE_CODES = {
    1: "q",
    2: "k",
    3: "b",
    4: "p",
    5: "n",
    6: "R",
    7: "P",
    8: "r",
    9: "B",
    10: "N",
    11: "Q",
    12: "K",
}


class UnknownPieceCode(ValueError):
    """A placement nibble outside :data:`PIECE_CODES` (sensor glitch or new firmware)."""


def decode_board(frame: bytes) -> chess.BaseBoard | None:
    """Piece placement carried by a board frame, ``None`` for any other frame."""
    if len(frame) < 34 or frame[:2] != BOARD_HEADER:
        return None
    board = chess.BaseBoard.empty()
    for i, byte in enumerate(frame[2:34]):
        rank, pair = divmod(i, 4)
        for file, code in ((2 * pair, byte & 0x0F), (2 * pair + 1, byte >> 4)):
            if code == 0:
                continue
            if code not in PIECE_CODES:
                raise UnknownPieceCode(
                    f"nibble {code:#x} on {chess.square_name(chess.square(file, rank))}"
                )
            board.set_piece_at(chess.square(file, rank), chess.Piece.from_symbol(PIECE_CODES[code]))
    return board


def led_frame(squares: Iterable[chess.Square]) -> bytes:
    """Command lighting exactly ``squares`` (an empty iterable switches all LEDs off)."""
    ranks = bytearray(8)
    for square in squares:
        ranks[chess.square_rank(square)] |= 1 << chess.square_file(square)
    return LED_HEADER + bytes(ranks)
