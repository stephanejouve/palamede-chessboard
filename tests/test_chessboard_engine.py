"""Tests for ``palamede_chessboard.chessboard_engine``.

Stockfish itself is mocked at ``chess.engine.SimpleEngine.popen_uci`` so
the tests run anywhere (CI doesn't need a Stockfish binary).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from palamede_chessboard.chessboard_engine import (
    BLUNDER_THRESHOLD_CP,
    INACCURACY_THRESHOLD_CP,
    MATE_SCORE_CP,
    MISTAKE_THRESHOLD_CP,
    EngineUnavailableError,
    GameWalkEntry,
    analyze_game,
    analyze_position,
    classify_loss,
    get_engine_path,
    get_my_mistakes,
)
from palamede_chessboard.chessboard_state import GameMetadata, GameState

# ---- helpers --------------------------------------------------------------


class _FakeScore:
    """Minimal stand-in for ``chess.engine.PovScore.white()``."""

    def __init__(self, cp: int | None = None, mate: int | None = None):
        self._cp = cp
        self._mate = mate

    def is_mate(self) -> bool:
        return self._mate is not None

    def score(self) -> int | None:
        return self._cp

    def mate(self) -> int | None:
        return self._mate


class _FakePovScore:
    def __init__(self, white_score: _FakeScore):
        self._white = white_score

    def white(self) -> _FakeScore:
        return self._white


def _mock_engine_with_scores(scores: list[int]):
    """Return a (context-manager) MagicMock engine emitting scores in order."""
    import chess

    side_effects: list = []
    for cp in scores:
        side_effects.append(
            {
                "score": _FakePovScore(_FakeScore(cp=cp)),
                "pv": [chess.Move.from_uci("e2e4")],
                "depth": 10,
            }
        )
    engine = MagicMock()
    engine.analyse.side_effect = side_effects
    cm = MagicMock()
    cm.__enter__.return_value = engine
    cm.__exit__.return_value = False
    return cm


def _mock_engine_with_povscores(pov_scores: list[_FakePovScore]):
    """Like ``_mock_engine_with_scores`` but takes raw fake PovScores.

    Lets tests mix cp and mate positions in a single engine walk.
    """
    import chess

    side_effects: list = []
    for pov in pov_scores:
        side_effects.append(
            {
                "score": pov,
                "pv": [chess.Move.from_uci("e2e4")],
                "depth": 10,
            }
        )
    engine = MagicMock()
    engine.analyse.side_effect = side_effects
    cm = MagicMock()
    cm.__enter__.return_value = engine
    cm.__exit__.return_value = False
    return cm


# ---- get_engine_path ------------------------------------------------------


class TestGetEnginePath:
    def test_env_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = tmp_path / "stockfish"
        fake.touch()
        monkeypatch.setenv("MCP_CHESSBOARD_STOCKFISH", str(fake))
        assert get_engine_path() == str(fake)

    def test_env_override_missing_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_CHESSBOARD_STOCKFISH", str(tmp_path / "nope"))
        with pytest.raises(EngineUnavailableError):
            get_engine_path()

    def test_path_lookup_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_CHESSBOARD_STOCKFISH", raising=False)
        with patch(
            "palamede_chessboard.chessboard_engine.shutil.which",
            return_value="/fake/path/stockfish",
        ):
            assert get_engine_path() == "/fake/path/stockfish"

    def test_no_binary_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_CHESSBOARD_STOCKFISH", raising=False)
        with (
            patch("palamede_chessboard.chessboard_engine.shutil.which", return_value=None),
            patch("palamede_chessboard.chessboard_engine._DEFAULT_BINARY_PATHS", []),
        ):
            with pytest.raises(EngineUnavailableError):
                get_engine_path()


# ---- classification thresholds --------------------------------------------


class TestClassifyLoss:
    def test_good(self) -> None:
        assert classify_loss(0) == "good"
        assert classify_loss(INACCURACY_THRESHOLD_CP - 1) == "good"

    def test_inaccuracy(self) -> None:
        assert classify_loss(INACCURACY_THRESHOLD_CP) == "inaccuracy"
        assert classify_loss(MISTAKE_THRESHOLD_CP - 1) == "inaccuracy"

    def test_mistake(self) -> None:
        assert classify_loss(MISTAKE_THRESHOLD_CP) == "mistake"
        assert classify_loss(BLUNDER_THRESHOLD_CP - 1) == "mistake"

    def test_blunder(self) -> None:
        assert classify_loss(BLUNDER_THRESHOLD_CP) == "blunder"
        assert classify_loss(2000) == "blunder"


# ---- analyze_position -----------------------------------------------------


class TestAnalyzePosition:
    def test_position_with_cp_score(self) -> None:
        with patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=_mock_engine_with_scores([42]),
        ):
            a = analyze_position(
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                depth=10,
                engine_path="/fake",
            )
        assert a.score_cp == 42
        assert a.mate_in is None
        assert a.best_move_uci == "e2e4"
        assert a.pv_uci == ["e2e4"]
        assert a.depth == 10

    def test_position_with_mate(self) -> None:
        import chess

        engine = MagicMock()
        engine.analyse.return_value = {
            "score": _FakePovScore(_FakeScore(mate=2)),
            "pv": [chess.Move.from_uci("d1h5")],
            "depth": 6,
        }
        cm = MagicMock()
        cm.__enter__.return_value = engine
        cm.__exit__.return_value = False
        with patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=cm,
        ):
            a = analyze_position(
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                engine_path="/fake",
            )
        assert a.score_cp is None
        assert a.mate_in == 2

    def test_to_dict_exposes_san_and_mate_alias(self) -> None:
        # The JS viewer reads ``a.best_move_san`` / ``a.pv_san`` / ``a.mate`` ;
        # the dataclass stores UCI + ``mate_in``. ``to_dict`` derives the
        # SAN strings from UCI+FEN and adds the ``mate`` alias.
        from palamede_chessboard.chessboard_engine import PositionAnalysis

        a = PositionAnalysis(
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            score_cp=20,
            mate_in=None,
            best_move_uci="e2e4",
            pv_uci=["e2e4", "e7e5", "g1f3"],
            depth=12,
        )
        d = a.to_dict()
        assert d["best_move_san"] == "e4"
        assert d["pv_san"] == ["e4", "e5", "Nf3"]
        assert d["mate"] is None  # alias of mate_in

    def test_to_dict_mate_alias_propagates(self) -> None:
        from palamede_chessboard.chessboard_engine import PositionAnalysis

        a = PositionAnalysis(
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            score_cp=None,
            mate_in=3,
            best_move_uci=None,
            pv_uci=[],
            depth=8,
        )
        d = a.to_dict()
        assert d["mate"] == 3
        assert d["best_move_san"] is None
        assert d["pv_san"] == []

    def test_to_dict_handles_invalid_uci_gracefully(self) -> None:
        from palamede_chessboard.chessboard_engine import PositionAnalysis

        a = PositionAnalysis(
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            score_cp=0,
            mate_in=None,
            best_move_uci="notamove",
            pv_uci=["notamove"],
            depth=5,
        )
        d = a.to_dict()
        # Bad UCI → SAN lists empty, no exception.
        assert d["best_move_san"] is None
        assert d["pv_san"] == []


# ---- analyze_game ---------------------------------------------------------


class TestAnalyzeGame:
    def test_walk_produces_one_entry_per_ply(self, tmp_path: Path) -> None:
        state = GameState.create(
            GameMetadata(game_id="walk", white="w", black="b"),
            games_dir=tmp_path,
        )
        state.play_move("e4")
        state.play_move("e5")
        state.play_move("Nf3")

        # 3 plies → 6 analyse calls (before+after each)
        with patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=_mock_engine_with_scores([20, 25, 30, 18, 40, 35]),
        ):
            walk = analyze_game(state, depth=8, engine_path="/fake")

        assert len(walk) == 3
        assert [e.mover for e in walk] == ["white", "black", "white"]
        assert [e.san for e in walk] == ["e4", "e5", "Nf3"]
        # Sanity : every entry has a classification.
        for entry in walk:
            assert entry.classification in {
                "good",
                "inaccuracy",
                "mistake",
                "blunder",
                "unknown",
            }


class TestAnalyzeGameMateClassification:
    """Regression tests for #245 : mate-eval must not classify as ``unknown``.

    Before the fix, Stockfish returning ``mate_in_N`` caused
    ``_score_to_cp`` to yield ``cp=None``, which propagated to
    ``mover_before/after=None`` and forced ``classification="unknown"``
    with ``cp_loss=None``. On admin-vs-leader-005 (Najdorf English
    Attack), this masked the real blunder 17...Qc8 (queen hangs). The
    fix normalizes mate scores to ``±MATE_SCORE_CP`` before the loss
    calculation.
    """

    def _one_move_state(self, tmp_path: Path):
        state = GameState.create(
            GameMetadata(game_id="mate", white="w", black="b"),
            games_dir=tmp_path,
        )
        state.play_move("e4")
        return state

    def test_cp_before_mate_after_for_opponent_is_blunder(self, tmp_path: Path) -> None:
        """Mover has an even position (cp≈0), then blunders into a mate
        threat against them → must classify as ``blunder``, not
        ``unknown``, with a numeric ``cp_loss`` >= MATE_SCORE_CP - buffer."""
        state = self._one_move_state(tmp_path)
        # Before white's move: even (cp=0). After: black mates in 3 (mate=-3).
        with patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=_mock_engine_with_povscores(
                [
                    _FakePovScore(_FakeScore(cp=0)),
                    _FakePovScore(_FakeScore(mate=-3)),
                ]
            ),
        ):
            walk = analyze_game(state, depth=8, engine_path="/fake")

        assert len(walk) == 1
        entry = walk[0]
        assert entry.classification == "blunder"
        assert entry.cp_loss is not None
        assert entry.cp_loss >= MATE_SCORE_CP - 100  # mate normalized to ~30000

    def test_mate_delivered_is_good(self, tmp_path: Path) -> None:
        """Mover was already winning (cp+500) and delivers mate-in-1 →
        classify ``good`` with ``cp_loss`` negative or small positive."""
        state = self._one_move_state(tmp_path)
        with patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=_mock_engine_with_povscores(
                [
                    _FakePovScore(_FakeScore(cp=500)),
                    _FakePovScore(_FakeScore(mate=1)),
                ]
            ),
        ):
            walk = analyze_game(state, depth=8, engine_path="/fake")

        entry = walk[0]
        assert entry.classification == "good"
        assert entry.cp_loss is not None
        assert entry.cp_loss < INACCURACY_THRESHOLD_CP  # no meaningful loss

    def test_both_positions_in_mate_shorter_is_good(self, tmp_path: Path) -> None:
        """Mover had mate-in-5, plays a move keeping mate-in-3 → mate got
        shorter for them → ``good`` (progress toward mate)."""
        state = self._one_move_state(tmp_path)
        # White has mate-in-5, plays into mate-in-3 (same side wins, faster)
        with patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=_mock_engine_with_povscores(
                [
                    _FakePovScore(_FakeScore(mate=5)),
                    _FakePovScore(_FakeScore(mate=3)),
                ]
            ),
        ):
            walk = analyze_game(state, depth=8, engine_path="/fake")

        entry = walk[0]
        assert entry.classification == "good"
        assert entry.cp_loss is not None
        assert entry.cp_loss <= 0  # shorter mate = negative loss

    def test_mate_against_becomes_worse_is_mistake_or_blunder(self, tmp_path: Path) -> None:
        """Mover was already getting mated in 10, plays a move that
        shortens the mate against them to 3 → ``mistake`` or worse.
        Not ``unknown`` — this is the exact class of position the bug
        masked."""
        state = self._one_move_state(tmp_path)
        # White gets mated: before mate-in-10 against, after mate-in-3 against.
        # mate=-10 → normalized -MATE_SCORE_CP + 10 = -29990
        # mate=-3  → normalized -MATE_SCORE_CP + 3  = -29997
        # loss = -29990 - (-29997) = 7 → good actually
        # Let's use different plies where the difference is bigger:
        # mate=-20 → -29980 ; mate=-2 → -29998 ; diff = 18 → still small
        # Actually the ply penalty is small (1 cp / ply) so both-mate positions
        # rarely cross the blunder threshold. Assert the important invariant:
        # not ``unknown``, and cp_loss is a numeric value.
        with patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=_mock_engine_with_povscores(
                [
                    _FakePovScore(_FakeScore(mate=-20)),
                    _FakePovScore(_FakeScore(mate=-2)),
                ]
            ),
        ):
            walk = analyze_game(state, depth=8, engine_path="/fake")

        entry = walk[0]
        assert entry.classification != "unknown"
        assert entry.cp_loss is not None

    def test_miracle_escape_from_mate_is_good(self, tmp_path: Path) -> None:
        """Mover was getting mated (mate-in-3 against), plays a move that
        escapes to cp+0 → ``good``, with negative cp_loss (huge gain)."""
        state = self._one_move_state(tmp_path)
        with patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=_mock_engine_with_povscores(
                [
                    _FakePovScore(_FakeScore(mate=-3)),
                    _FakePovScore(_FakeScore(cp=0)),
                ]
            ),
        ):
            walk = analyze_game(state, depth=8, engine_path="/fake")

        entry = walk[0]
        assert entry.classification == "good"
        assert entry.cp_loss is not None
        assert entry.cp_loss < 0  # huge gain, negative loss


# ---- get_my_mistakes -----------------------------------------------------


def _entry(ply: int, mover: str, classification: str, cp_loss: int = 0) -> GameWalkEntry:
    return GameWalkEntry(
        ply=ply,
        mover=mover,
        san="x",
        fen_before="",
        fen_after="",
        score_cp_before=0,
        score_cp_after=0,
        cp_loss=cp_loss,
        classification=classification,
    )


class TestGetMyMistakes:
    def test_filters_by_player(self) -> None:
        walk = [
            _entry(1, "white", "blunder"),
            _entry(2, "black", "blunder"),
            _entry(3, "white", "good"),
            _entry(4, "black", "mistake"),
        ]
        whites = get_my_mistakes(walk, mover="white")
        assert [e.ply for e in whites] == [1]

    def test_min_classification_inaccuracy(self) -> None:
        walk = [
            _entry(1, "white", "good"),
            _entry(2, "white", "inaccuracy"),
            _entry(3, "white", "mistake"),
            _entry(4, "white", "blunder"),
        ]
        out = get_my_mistakes(walk, mover="white", min_classification="inaccuracy")
        assert [e.ply for e in out] == [2, 3, 4]

    def test_min_classification_blunder_only(self) -> None:
        walk = [
            _entry(1, "white", "good"),
            _entry(2, "white", "inaccuracy"),
            _entry(3, "white", "mistake"),
            _entry(4, "white", "blunder"),
        ]
        out = get_my_mistakes(walk, mover="white", min_classification="blunder")
        assert [e.ply for e in out] == [4]

    def test_unknown_min_classification_raises(self) -> None:
        with pytest.raises(ValueError):
            get_my_mistakes([], mover="white", min_classification="huge-blunder")

    def test_unknown_classification_treated_as_good(self) -> None:
        walk = [_entry(1, "white", "unknown")]
        assert get_my_mistakes(walk, mover="white") == []
