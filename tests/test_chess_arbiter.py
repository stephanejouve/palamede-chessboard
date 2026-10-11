"""Tests for :mod:`palamede_chessboard.chess_arbiter`."""

from __future__ import annotations

import chess

from palamede_chessboard.chess_arbiter import (
    ValidationResult,
    move_request_context,
    validate_san_strict,
)

# ---- validate_san_strict ------------------------------------------------


def _board_from(sans: list[str]) -> chess.Board:
    b = chess.Board()
    for san in sans:
        b.push_san(san)
    return b


def test_reject_ghost_capture_bxb6_partie_004_regression() -> None:
    """Reproduces partie 004 tour 16 : ``Bxb6`` sur cavalier absent."""
    b = _board_from(
        [
            "e4",
            "c5",
            "Nf3",
            "d6",
            "d4",
            "cxd4",
            "Nxd4",
            "Nf6",
            "Nc3",
            "a6",
            "Be3",
            "e5",
            "Nb3",
            "Be6",
            "f3",
            "h5",
            "Qd2",
            "Nbd7",
            "O-O-O",
            "b5",
            "Kb1",
            "Nb6",
            "Na5",
            "Rc8",
            "Nd5",
            "Bxd5",
            "exd5",
            "Nc4",
            "Nxc4",
            "bxc4",
        ]
    )
    assert b.piece_at(chess.parse_square("b6")) is None

    result = validate_san_strict(b, "Bxb6")

    assert result.ok is False
    assert "case b6" in result.reason
    assert "vide" in result.reason


def test_accept_legal_move_with_correct_capture_suffix() -> None:
    b = _board_from(["e4", "d5"])
    result = validate_san_strict(b, "exd5")
    assert result == ValidationResult(True, "")


def test_reject_missing_x_when_capture() -> None:
    b = _board_from(["e4", "d5"])
    result = validate_san_strict(b, "ed5")
    # python-chess parses "ed5" tolerantly as the capture "exd5" — we must
    # still reject it for missing 'x'.
    assert result.ok is False
    assert "sans 'x'" in result.reason


def test_reject_plus_without_check() -> None:
    b = chess.Board()
    result = validate_san_strict(b, "e4+")
    assert result.ok is False
    assert "sans échec" in result.reason


def test_accept_plus_with_check() -> None:
    b = _board_from(["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "Ng5", "d5", "exd5"])
    result = validate_san_strict(b, "Nxd5")
    assert result.ok is True


def test_reject_missing_plus_when_check() -> None:
    b = _board_from(
        [
            "e4",
            "c5",
            "Nf3",
            "d6",
            "d4",
            "cxd4",
            "Nxd4",
            "Nf6",
            "Nc3",
            "a6",
            "Be3",
            "e5",
            "Nb3",
            "Be6",
            "f3",
            "h5",
            "Qd2",
            "Nbd7",
            "O-O-O",
            "b5",
            "Kb1",
            "Nb6",
            "Na5",
            "Rc8",
            "Nd5",
            "Bxd5",
            "exd5",
            "Nc4",
            "Nxc4",
            "bxc4",
            "g4",
            "hxg4",
            "fxg4",
            "Nxg4",
            "Bg5",
            "f6",
            "Bf4",
            "Be7",
            "Rg1",
            "Nh6",
            "Bxh6",
            "gxh6",
            "Bh3",
            "c3",
            "bxc3",
        ]
    )
    # 23...Rb8+ delivers check ; annotator forgets '+'.
    result = validate_san_strict(b, "Rb8")
    assert result.ok is False
    assert "sans '+'" in result.reason


def test_reject_hash_without_mate() -> None:
    b = chess.Board()
    result = validate_san_strict(b, "e4#")
    assert result.ok is False
    assert "sans échec et mat" in result.reason


def test_accept_hash_with_mate() -> None:
    # Fool's mate.
    b = _board_from(["f3", "e5", "g4"])
    result = validate_san_strict(b, "Qh4#")
    assert result.ok is True


def test_reject_illegal_move() -> None:
    b = chess.Board()
    result = validate_san_strict(b, "Ke5")
    assert result.ok is False
    assert "illegal SAN" in result.reason


def test_board_not_mutated_after_validation() -> None:
    b = chess.Board()
    fen_before = b.fen()
    validate_san_strict(b, "e4")
    validate_san_strict(b, "Nf3")
    validate_san_strict(b, "e4#")  # rejected
    assert b.fen() == fen_before


# ---- move_request_context -----------------------------------------------


def test_context_initial_position() -> None:
    b = chess.Board()
    ctx = move_request_context(b)
    assert ctx == {
        "fen": chess.STARTING_FEN,
        "last_moves": [],
        "ply": 0,
        "turn": "white",
    }


def test_context_last_moves_ordered_oldest_first() -> None:
    b = _board_from(["e4", "c5", "Nf3", "d6", "d4", "cxd4"])
    ctx = move_request_context(b, last_n=4)
    assert ctx["last_moves"] == ["Nf3", "d6", "d4", "cxd4"]
    assert ctx["ply"] == 6
    assert ctx["turn"] == "white"


def test_context_truncates_when_history_shorter_than_n() -> None:
    b = _board_from(["e4", "e5"])
    ctx = move_request_context(b, last_n=10)
    assert ctx["last_moves"] == ["e4", "e5"]


def test_context_last_n_zero_returns_no_moves() -> None:
    b = _board_from(["e4", "e5"])
    ctx = move_request_context(b, last_n=0)
    assert ctx["last_moves"] == []


def test_context_turn_black_after_white_move() -> None:
    b = _board_from(["e4"])
    ctx = move_request_context(b)
    assert ctx["turn"] == "black"
