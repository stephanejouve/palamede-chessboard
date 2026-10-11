"""Stockfish engine wrapper for ``mcp_chessboard_server`` (Phase B).

Local UCI engine via ``chess.engine.SimpleEngine.popen_uci``. Replaces
the original ``chess-api.com`` plan with a Homebrew-installed Stockfish
binary, avoiding network quota and giving sub-second analyses.

The wrapper exposes three concerns :

* **Position eval** — ``analyze_position`` returns score (centipawns,
  ``+`` = white better) plus best move + principal variation.
* **Game walk** — ``analyze_game`` iterates over every ply in a game's
  PGN history and returns an array of per-position evaluations, plus
  move classification (blunder / mistake / inaccuracy / good).
* **Mistake mining** — ``get_my_mistakes`` filters the walk by player
  identity, returning the subset of moves that lost ≥ 50 cp.

The Stockfish binary path is auto-detected from common Homebrew prefixes
(``/usr/local/bin`` Intel, ``/opt/homebrew/bin`` ARM) and overridable
via ``MCP_CHESSBOARD_STOCKFISH``.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.engine

from palamede_chessboard.chessboard_state import GameState

logger = logging.getLogger(__name__)

#: Lichess-style centipawn-loss thresholds for move classification.
#: A move is classified by ``score(opponent_pov_after) - score(opponent_pov_before)``
#: i.e. how much *worse* the mover's evaluation is after the move.
BLUNDER_THRESHOLD_CP = 300
MISTAKE_THRESHOLD_CP = 100
INACCURACY_THRESHOLD_CP = 50

#: Mate positions are normalized to a white-POV centipawn value near this
#: magnitude, with a small ply-distance penalty. Chosen well above any
#: realistic cp evaluation so classification cannot confuse mate swings
#: with material advantages. Convention: mate_in_N (positive = white
#: mates) → ``+MATE_SCORE_CP - N``, mate against white → ``-MATE_SCORE_CP + N``.
MATE_SCORE_CP = 30000

DEFAULT_DEPTH = 15
DEFAULT_TIMEOUT_SECONDS = 10.0

#: Fallback locations probed in order when neither env override nor PATH match.
_DEFAULT_BINARY_PATHS = [
    "/opt/homebrew/bin/stockfish",  # macOS ARM (Homebrew default)
    "/usr/local/bin/stockfish",  # macOS Intel + Linuxbrew
    "/usr/bin/stockfish",  # Linux distros
]


class EngineUnavailableError(RuntimeError):
    """Raised when no Stockfish binary can be located."""


def get_engine_path() -> str:
    """Return the path to a Stockfish binary.

    Resolution order :

    1. ``MCP_CHESSBOARD_STOCKFISH`` env var.
    2. First match on ``$PATH`` (``shutil.which("stockfish")``).
    3. First existing path in :data:`_DEFAULT_BINARY_PATHS`.

    Raises :class:`EngineUnavailableError` if none resolves.
    """
    override = os.environ.get("MCP_CHESSBOARD_STOCKFISH", "").strip()
    if override:
        if Path(override).is_file():
            return override
        raise EngineUnavailableError(
            f"MCP_CHESSBOARD_STOCKFISH points at {override!r} which does not exist"
        )

    on_path = shutil.which("stockfish")
    if on_path:
        return on_path

    for candidate in _DEFAULT_BINARY_PATHS:
        if Path(candidate).is_file():
            return candidate

    raise EngineUnavailableError(
        "Stockfish binary not found. Install via `brew install stockfish` "
        "or set MCP_CHESSBOARD_STOCKFISH."
    )


@dataclass
class PositionAnalysis:
    """Single-position evaluation snapshot returned by :func:`analyze_position`."""

    fen: str
    score_cp: int | None  # centipawns from white's POV, None if mate found
    mate_in: int | None  # plies-to-mate (positive = white mates), else None
    best_move_uci: str | None
    pv_uci: list[str]
    depth: int

    def to_dict(self) -> dict:
        # SAN variants are computed from UCI + FEN at serialization time
        # so the client UI can display human-readable moves without having
        # to track the board state itself. ``mate`` is an alias of
        # ``mate_in`` kept for backward-compatibility with the JS viewer
        # that already reads ``a.mate``.
        try:
            board = chess.Board(self.fen)
            pv_san: list[str] = []
            best_san: str | None = None
            for i, uci in enumerate(self.pv_uci):
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    break
                san = board.san(move)
                pv_san.append(san)
                if i == 0:
                    best_san = san
                board.push(move)
        except (ValueError, IndexError):
            pv_san = []
            best_san = None
        return {
            "fen": self.fen,
            "score_cp": self.score_cp,
            "mate_in": self.mate_in,
            "mate": self.mate_in,
            "best_move_uci": self.best_move_uci,
            "best_move_san": best_san,
            "pv_uci": list(self.pv_uci),
            "pv_san": pv_san,
            "depth": self.depth,
        }


def _score_to_cp(score: chess.engine.PovScore) -> tuple[int | None, int | None]:
    """Translate a python-chess score to (centipawns_white_pov, mate_in_plies)."""
    white_pov = score.white()
    if white_pov.is_mate():
        return (None, white_pov.mate())
    return (white_pov.score(), None)


def _score_to_normalized_cp(score: chess.engine.PovScore) -> int | None:
    """Return a white-POV centipawn value with mate mapped to ``±MATE_SCORE_CP``.

    Mate-in-N (positive N = white mates) → ``+MATE_SCORE_CP - N``; mate
    against white (negative N) → ``-MATE_SCORE_CP - N``. The ply-distance
    penalty preserves ordering (mate-in-1 > mate-in-5 for the winning side)
    without collapsing all mate positions onto the same value.

    Returns ``None`` only if the engine returned a cp score of ``None``
    for a non-mate position, which shouldn't happen in normal Stockfish
    output but is preserved for safety.
    """
    white_pov = score.white()
    if white_pov.is_mate():
        mate_in = white_pov.mate()
        assert mate_in is not None, "is_mate() true but mate() returned None"
        if mate_in > 0:
            return MATE_SCORE_CP - mate_in
        return -MATE_SCORE_CP - mate_in
    return white_pov.score()


def analyze_position(
    fen: str,
    *,
    depth: int = DEFAULT_DEPTH,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    engine_path: str | None = None,
) -> PositionAnalysis:
    """Run Stockfish on ``fen`` at ``depth`` plies. Synchronous + blocking."""
    board = chess.Board(fen)
    path = engine_path or get_engine_path()
    limit = chess.engine.Limit(depth=depth, time=timeout)
    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        info = engine.analyse(board, limit)
    score = info.get("score")
    if score is None:
        cp, mate = None, None
    else:
        cp, mate = _score_to_cp(score)
    pv = info.get("pv") or []
    return PositionAnalysis(
        fen=fen,
        score_cp=cp,
        mate_in=mate,
        best_move_uci=pv[0].uci() if pv else None,
        pv_uci=[mv.uci() for mv in pv[:8]],
        depth=int(info.get("depth", depth)),
    )


def classify_loss(cp_loss: int) -> str:
    """Classify a centipawn loss using Lichess-style thresholds.

    ``cp_loss`` is *positive* when the move was bad : ``score_before -
    score_after`` from the mover's POV.
    """
    if cp_loss >= BLUNDER_THRESHOLD_CP:
        return "blunder"
    if cp_loss >= MISTAKE_THRESHOLD_CP:
        return "mistake"
    if cp_loss >= INACCURACY_THRESHOLD_CP:
        return "inaccuracy"
    return "good"


@dataclass
class GameWalkEntry:
    """One ply in a game-walk analysis."""

    ply: int
    mover: str  # "white" / "black"
    san: str
    fen_before: str
    fen_after: str
    score_cp_before: int | None
    score_cp_after: int | None
    cp_loss: int | None  # mover-POV centipawn loss
    classification: str  # blunder / mistake / inaccuracy / good / unknown

    def to_dict(self) -> dict:
        return {
            "ply": self.ply,
            "mover": self.mover,
            "san": self.san,
            "fen_before": self.fen_before,
            "fen_after": self.fen_after,
            "score_cp_before": self.score_cp_before,
            "score_cp_after": self.score_cp_after,
            "cp_loss": self.cp_loss,
            "classification": self.classification,
        }


def _mover_cp(score_white_pov: int | None, mover: str) -> int | None:
    if score_white_pov is None:
        return None
    return score_white_pov if mover == "white" else -score_white_pov


def analyze_game(
    state: GameState,
    *,
    depth: int = DEFAULT_DEPTH,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    engine_path: str | None = None,
) -> list[GameWalkEntry]:
    """Walk through every move in ``state`` and evaluate before/after.

    Returns one entry per ply. ``cp_loss`` is the mover-POV centipawn
    drop between the evaluation BEFORE their move and AFTER. ``mate_in``
    positions are mapped to a very large cp value so they always
    classify as "good" for the side delivering mate.
    """
    path = engine_path or get_engine_path()
    limit = chess.engine.Limit(depth=depth, time=timeout)
    entries: list[GameWalkEntry] = []
    board = chess.Board()
    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        for ply_index, san in enumerate(state.san_history):
            mover = "white" if board.turn == chess.WHITE else "black"
            fen_before = board.fen()
            info_before = engine.analyse(board, limit)
            score_before = info_before.get("score")
            if score_before is not None:
                cp_before_white, _mate_before = _score_to_cp(score_before)
                normalized_before = _score_to_normalized_cp(score_before)
            else:
                cp_before_white, normalized_before = None, None

            move = board.parse_san(san)
            board.push(move)

            fen_after = board.fen()
            info_after = engine.analyse(board, limit)
            score_after = info_after.get("score")
            if score_after is not None:
                cp_after_white, _mate_after = _score_to_cp(score_after)
                normalized_after = _score_to_normalized_cp(score_after)
            else:
                cp_after_white, normalized_after = None, None

            # Classification uses normalized scores so mate transitions
            # produce meaningful cp_loss / classification (fix #245).
            mover_before = _mover_cp(normalized_before, mover)
            mover_after = _mover_cp(normalized_after, mover)
            if mover_before is None or mover_after is None:
                cp_loss = None
                classification = "unknown"
            else:
                cp_loss = mover_before - mover_after
                classification = classify_loss(max(0, cp_loss))

            entries.append(
                GameWalkEntry(
                    ply=ply_index + 1,
                    mover=mover,
                    san=san,
                    fen_before=fen_before,
                    fen_after=fen_after,
                    score_cp_before=cp_before_white,
                    score_cp_after=cp_after_white,
                    cp_loss=cp_loss,
                    classification=classification,
                )
            )
    return entries


def get_my_mistakes(
    walk: list[GameWalkEntry],
    *,
    mover: str,
    min_classification: str = "inaccuracy",
) -> list[GameWalkEntry]:
    """Filter a game walk down to one player's sub-optimal moves."""
    order = ["good", "inaccuracy", "mistake", "blunder"]
    if min_classification not in order:
        raise ValueError(f"Unknown classification: {min_classification!r}")
    min_idx = order.index(min_classification)
    return [
        e
        for e in walk
        if e.mover == mover
        and order.index(e.classification if e.classification in order else "good") >= min_idx
    ]
