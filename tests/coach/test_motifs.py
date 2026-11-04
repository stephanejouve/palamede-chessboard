"""The motif of a mistake, read on the board (#95). Synthetic positions only."""

from __future__ import annotations

import chess
import pytest

from palamede_chessboard.chessboard_engine import GameWalkEntry
from palamede_chessboard.coach.motifs import HEURISTIC_MOTIFS, MOTIFS, motif_of


def _entry(fen: str, san: str, missed_mate: int | None = None) -> GameWalkEntry:
    board = chess.Board(fen)
    mover = "white" if board.turn == chess.WHITE else "black"
    after = board.copy()
    after.push_san(san)
    return GameWalkEntry(
        ply=1,
        mover=mover,
        san=san,
        fen_before=fen,
        fen_after=after.fen(),
        score_cp_before=0,
        score_cp_after=-500,
        cp_loss=500,
        classification="blunder",
        missed_mate=missed_mate,
    )


#: 1.e4 d5, white to move : the c8 bishop sees g4.
AFTER_E4_D5 = "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2"
#: The f3 knight is attacked by the e4 pawn.
KNIGHT_ATTACKED = "rnbqkbnr/pppp1ppp/8/8/4p3/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 3"
#: White castled short, nothing en prise.
CASTLED = "r1bqkbnr/pppppppp/2n5/8/8/5N2/PPPPPPPP/RNBQ1RK1 w kq - 0 4"


@pytest.mark.parametrize(
    ("fen", "san", "missed_mate", "motif"),
    [
        (AFTER_E4_D5, "exd5", 1, "missed_mate"),
        (AFTER_E4_D5, "Qg4", None, "hanging_piece"),  # the bishop takes the queen
        (KNIGHT_ATTACKED, "a3", None, "ignored_threat"),
        (KNIGHT_ATTACKED, "Nd4", None, "other"),  # the knight goes, the threat is met
        (chess.STARTING_FEN, "e4", None, "other"),
        (CASTLED, "h3", None, "weakened_king"),
        (CASTLED, "g3", None, "weakened_king"),
        (CASTLED, "a3", None, "other"),  # not a pawn of the shelter
        ("r1bqkbnr/pppppppp/2n5/8/8/2N5/PPPPPPPP/R1BQ1RK1 w kq - 0 4", "f3", None, "weakened_king"),
        # A king off its back rank has no shelter : an endgame pawn move.
        ("8/8/8/8/8/6K1/6P1/k7 w - - 0 40", "Kf3", None, "other"),
        ("8/8/8/8/8/5PK1/8/k7 w - - 0 40", "f4", None, "other"),
        # Castling keeps the king safe : not a king that walks.
        (
            "rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 0 5",
            "O-O",
            None,
            "other",
        ),
        # A queen out at move 12 is no longer « early ».
        ("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 12", "Qh5", None, "other"),
        (
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
            "Ke2",
            None,
            "weakened_king",
        ),
        ("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "Qh5", None, "lone_queen"),
        # Two minor pieces out : the queen is no longer alone.
        (
            "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
            "Qe2",
            None,
            "other",
        ),
    ],
)
def test_motif_of(fen: str, san: str, missed_mate: int | None, motif: str) -> None:
    assert motif_of(_entry(fen, san, missed_mate)) == motif


def test_a_missed_mate_comes_before_a_piece_en_prise() -> None:
    assert motif_of(_entry(AFTER_E4_D5, "Qg4", missed_mate=2)) == "missed_mate"


def test_a_piece_en_prise_comes_before_a_lone_queen() -> None:
    # Qg4 is early and alone, but first of all en prise.
    assert motif_of(_entry(AFTER_E4_D5, "Qg4")) == "hanging_piece"


def test_the_heuristics_are_motifs() -> None:
    assert HEURISTIC_MOTIFS <= set(MOTIFS)
    assert HEURISTIC_MOTIFS == {"lone_queen", "weakened_king"}
