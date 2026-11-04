"""A move that mates is never a mistake ; a forced mate missed is said in moves (#100).

Stockfish scores a mated position ``mate 0`` for the side to move :
python-chess gives ``Mate(-0)`` when white is mated, ``MateGiven`` when
black is, both with ``mate() == 0``. Read as a mate against white,
every mate white delivered lost 59999 centipawns and became a blunder.
The scores below are real ``PovScore`` objects, as the engine returns
them : relative to the side to move.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import chess
import chess.engine
import pytest

from palamede_chessboard.chessboard_engine import (
    MATE_SCORE_CP,
    _score_to_normalized_cp,
    analyze_game,
    get_my_mistakes,
)
from palamede_chessboard.chessboard_state import GameMetadata, GameState

#: 1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6, white mates with Qxf7#.
SCHOLAR = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
#: 1.f3 e5 2.g4, black mates with Qh4#.
FOOL = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"

WHITE, BLACK = chess.WHITE, chess.BLACK


def _walk(tmp_path: Path, fen: str, san: str, before, after) -> list:
    state = GameState.create(
        GameMetadata(game_id="mate", white="w", black="b"), games_dir=tmp_path, starting_fen=fen
    )
    state.play_move(san)
    engine = MagicMock()
    engine.analyse.side_effect = [{"score": before}, {"score": after}]
    cm = MagicMock()
    cm.__enter__.return_value = engine
    target = "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci"
    with patch(target, return_value=cm):
        return analyze_game(state, depth=8, engine_path="/fake")


@pytest.mark.parametrize(("mated", "expected"), [(WHITE, -MATE_SCORE_CP), (BLACK, MATE_SCORE_CP)])
def test_a_mated_position_is_scored_for_the_side_that_mated(mated, expected) -> None:
    assert _score_to_normalized_cp(chess.engine.PovScore(chess.engine.Mate(0), mated)) == expected


def test_white_mating_is_no_mistake(tmp_path: Path) -> None:
    before = chess.engine.PovScore(chess.engine.Mate(1), WHITE)
    after = chess.engine.PovScore(chess.engine.Mate(0), BLACK)
    walk = _walk(tmp_path, SCHOLAR, "Qxf7#", before, after)
    entry = walk[0]
    assert entry.classification == "good" and entry.cp_loss == -1
    assert entry.missed_mate is None
    assert get_my_mistakes(walk, mover="white") == []


def test_black_mating_is_no_mistake(tmp_path: Path) -> None:
    before = chess.engine.PovScore(chess.engine.Mate(1), BLACK)
    after = chess.engine.PovScore(chess.engine.Mate(0), WHITE)
    walk = _walk(tmp_path, FOOL, "Qh4#", before, after)
    assert walk[0].classification == "good" and walk[0].missed_mate is None
    assert get_my_mistakes(walk, mover="black") == []


def test_a_slower_forced_mate_kept_is_no_mistake(tmp_path: Path) -> None:
    before = chess.engine.PovScore(chess.engine.Mate(3), WHITE)
    after = chess.engine.PovScore(chess.engine.Mate(-4), BLACK)
    entry = _walk(tmp_path, SCHOLAR, "Bb3", before, after)[0]
    assert entry.classification == "good" and entry.missed_mate is None


def test_a_forced_mate_missed_is_said_in_moves(tmp_path: Path) -> None:
    before = chess.engine.PovScore(chess.engine.Mate(1), WHITE)
    after = chess.engine.PovScore(chess.engine.Cp(900), BLACK)
    walk = _walk(tmp_path, SCHOLAR, "Qxe5+", before, after)
    entry = walk[0]
    assert entry.classification == "blunder"
    assert entry.missed_mate == 1
    assert entry.to_dict()["missed_mate"] == 1
    assert get_my_mistakes(walk, mover="white") == [entry]


def test_black_missing_a_forced_mate_is_said_from_black_s_side(tmp_path: Path) -> None:
    before = chess.engine.PovScore(chess.engine.Mate(1), BLACK)
    after = chess.engine.PovScore(chess.engine.Cp(0), WHITE)
    entry = _walk(tmp_path, FOOL, "Nc6", before, after)[0]
    assert entry.classification == "blunder" and entry.missed_mate == 1


def test_a_mate_suffered_is_no_mate_missed(tmp_path: Path) -> None:
    before = chess.engine.PovScore(chess.engine.Mate(-2), WHITE)
    after = chess.engine.PovScore(chess.engine.Mate(1), BLACK)
    assert _walk(tmp_path, SCHOLAR, "Bb3", before, after)[0].missed_mate is None
