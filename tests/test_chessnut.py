"""Chessnut Air+ bridge : wire format, move reading, bridge decisions.

The ``REAL_*`` frames were captured on an Air+ on 2026-10-23 (white pieces
only on the board, then 1.d4 played and taken back).
"""

from __future__ import annotations

import time

import chess
import pytest

from palamede_chessboard.chessnut.bridge import GUIDE_SECONDS, Bridge, decide
from palamede_chessboard.chessnut.moves import differing_squares, find_move
from palamede_chessboard.chessnut.protocol import (
    PIECE_CODES,
    UnknownPieceCode,
    decode_board,
    led_frame,
)

REAL_WHITE_ONLY = bytes.fromhex("0124a6b99c6a77777777" + "00" * 24 + "15000000")
REAL_D4 = bytes.fromhex("0124a6b99c6a77077777" + "0000000000700000" + "00" * 16 + "19000000")
CODES = {symbol: code for code, symbol in PIECE_CODES.items()}


def frame(board: chess.BaseBoard) -> bytes:
    """Board frame for ``board`` (inverse of :func:`decode_board`)."""
    placement = bytearray(32)
    for square, piece in board.piece_map().items():
        rank, file = chess.square_rank(square), chess.square_file(square)
        shift = 4 * (file % 2)
        placement[4 * rank + file // 2] |= CODES[piece.symbol()] << shift
    return bytes([0x01, 0x24]) + bytes(placement) + bytes(4)


def after(*sans: str, fen: str = chess.STARTING_FEN) -> chess.Board:
    board = chess.Board(fen)
    for san in sans:
        board.push_san(san)
    return board


def placement(board: chess.Board) -> chess.BaseBoard:
    return chess.BaseBoard(board.board_fen())


# ---- protocol --------------------------------------------------------------


def test_real_frames_decode() -> None:
    assert decode_board(REAL_WHITE_ONLY).board_fen() == "8/8/8/8/8/8/PPPPPPPP/RNBQKBNR"
    assert decode_board(REAL_D4).board_fen() == "8/8/8/8/3P4/8/PPP1PPPP/RNBQKBNR"


def test_start_position_round_trips() -> None:
    start = chess.BaseBoard()
    assert decode_board(frame(start)).board_fen() == start.board_fen()


def test_other_frames_are_ignored() -> None:
    assert decode_board(bytes([0x23, 0x01, 0x00])) is None
    assert decode_board(REAL_WHITE_ONLY[:20]) is None


def test_unknown_nibble_is_reported() -> None:
    with pytest.raises(UnknownPieceCode, match="a1"):
        decode_board(bytes([0x01, 0x24, 0x0F]) + bytes(35))


def test_led_frames_match_the_probe() -> None:
    # Probe of 2026-10-23 : byte 0 bit 0x01 → a1, bit 0x80 → h1, byte 7 bit 0x01 → a8.
    assert led_frame([chess.A1]) == bytes.fromhex("0a080100000000000000")
    assert led_frame([chess.H1]) == bytes.fromhex("0a088000000000000000")
    assert led_frame([chess.A8]) == bytes.fromhex("0a080000000000000001")
    assert led_frame([chess.E2, chess.E4]) == bytes.fromhex("0a080010001000000000")
    assert led_frame([]) == bytes.fromhex("0a080000000000000000")


# ---- moves -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("fen", "san"),
    [
        (chess.STARTING_FEN, "e4"),
        (after("e4", "d5").fen(), "exd5"),
        (after("e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6").fen(), "O-O"),
        (after("e4", "a6", "e5", "d5").fen(), "exd6"),
        ("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e8=Q"),
        ("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e8=N"),
    ],
)
def test_the_placement_decides_the_move(fen: str, san: str) -> None:
    game = chess.Board(fen)
    move = game.parse_san(san)
    assert find_move(game, placement(after(san, fen=fen))) == move


def test_a_lifted_piece_is_not_a_move() -> None:
    lifted = chess.BaseBoard()
    lifted.remove_piece_at(chess.G1)
    assert find_move(chess.Board(), lifted) is None


def test_differing_squares_point_at_the_move() -> None:
    assert differing_squares(chess.BaseBoard(), placement(after("e4"))) == {chess.E2, chess.E4}


# ---- decisions -------------------------------------------------------------


def test_nothing_happens_before_the_placement_settles() -> None:
    assert decide(chess.Board(), chess.WHITE, placement(after("e4")), 0.1) is None


def test_same_placement_switches_leds_off() -> None:
    assert decide(chess.Board(), chess.WHITE, chess.BaseBoard(), 1.0).leds == frozenset()


def test_own_move_is_posted() -> None:
    decision = decide(chess.Board(), chess.WHITE, placement(after("e4")), 1.0)
    assert decision.move == chess.Move.from_uci("e2e4")
    assert decision.leds == frozenset()


def test_held_piece_lights_nothing_until_the_guide_delay() -> None:
    lifted = chess.BaseBoard()
    lifted.remove_piece_at(chess.G1)
    assert decide(chess.Board(), chess.WHITE, lifted, 1.0).leds == frozenset()
    assert decide(chess.Board(), chess.WHITE, lifted, GUIDE_SECONDS).leds == {chess.G1}


def test_opponent_move_lights_up_until_replicated() -> None:
    game = after("e4", "e5")
    decision = decide(game, chess.WHITE, placement(after("e4")), 1.0, synced=False)
    assert decision.move is None
    assert decision.leds == {chess.E7, chess.E5}
    # Pawn lifted from e7, still in hand : e5 stays lit, no guide delay.
    in_hand = placement(after("e4"))
    in_hand.remove_piece_at(chess.E7)
    assert decide(game, chess.WHITE, in_hand, 1.0, synced=False).leds == {chess.E5}


def test_pending_move_is_not_lit_nor_reposted() -> None:
    decision = decide(chess.Board(), chess.WHITE, placement(after("e4")), 5.0, pending=True)
    assert decision == decide(chess.Board(), chess.WHITE, chess.BaseBoard(), 5.0)


def test_refused_move_lights_up_instead_of_being_reposted() -> None:
    refused = chess.Move.from_uci("e2e4")
    decision = decide(chess.Board(), chess.WHITE, placement(after("e4")), 1.0, refused=refused)
    assert decision.move is None
    assert decision.leds == {chess.E2, chess.E4}


def test_no_move_is_posted_once_the_game_is_over() -> None:
    decision = decide(chess.Board(), chess.WHITE, placement(after("e4")), 1.0, live=False)
    assert decision.move is None


# ---- bridge ----------------------------------------------------------------


def snapshot(board: chess.Board, *, over: bool = False) -> dict:
    turn = "white" if board.turn == chess.WHITE else "black"
    return {"fen": board.fen(), "ply": board.ply(), "turn": turn, "is_game_over": over}


async def test_tick_posts_the_move_and_lights_the_reply() -> None:
    bridge = Bridge("http://viewer.test", "g1", chess.WHITE, token="t")
    posted: list[str] = []
    bridge._post_move = lambda uci: posted.append(uci)  # type: ignore[method-assign]
    written: list[bytes] = []

    async def write(command: bytes) -> None:
        written.append(command)

    bridge.apply_stream(snapshot(chess.Board()))
    bridge.on_frame(None, bytearray(frame(placement(after("e4")))))
    bridge.settled_since = time.monotonic() - 1.0
    await bridge.tick(write)
    assert posted == ["e2e4"]
    assert written == [led_frame([])]

    bridge.apply_stream(snapshot(after("e4", "e5")))
    await bridge.tick(write)
    assert written[-1] == led_frame([chess.E7, chess.E5])


async def test_refused_move_is_not_posted_twice() -> None:
    bridge = Bridge("http://viewer.test", "g1", chess.WHITE, token="t")
    posted: list[str] = []

    def refuse(uci: str) -> str:
        posted.append(uci)
        return "HTTP 403 NOT_YOUR_TURN"

    bridge._post_move = refuse  # type: ignore[method-assign]

    async def write(command: bytes) -> None:
        pass

    bridge.apply_stream(snapshot(chess.Board()))
    bridge.on_frame(None, bytearray(frame(placement(after("e4")))))
    bridge.settled_since = time.monotonic() - 1.0
    await bridge.tick(write)
    await bridge.tick(write)
    assert posted == ["e2e4"]
    assert bridge.leds == {chess.E2, chess.E4}


def test_glitched_frame_is_dropped() -> None:
    bridge = Bridge("http://viewer.test", "g1", chess.WHITE, token="t")
    bridge.on_frame(None, bytearray([0x01, 0x24, 0x0F]) + bytearray(35))
    assert bridge.observed is None


def test_reconnection_snapshot_keeps_the_pending_move() -> None:
    bridge = Bridge("http://viewer.test", "g1", chess.WHITE, token="t")
    bridge.apply_stream(snapshot(chess.Board()))
    bridge.pending = chess.Move.from_uci("e2e4")
    bridge.apply_stream(snapshot(chess.Board()))
    assert bridge.pending is not None
    bridge.apply_stream(snapshot(after("e4")))
    assert bridge.pending is None
