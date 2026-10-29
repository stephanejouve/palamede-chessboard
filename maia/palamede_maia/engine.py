"""lc0 with the Maia weights of one level, one node per move.

One lc0 process per level, started on the first move asked of it and kept
open ; a process that fails is closed and started again on the next move.
"""

from __future__ import annotations

import logging
from pathlib import Path

import chess
import chess.engine

log = logging.getLogger(__name__)

#: Maia is trained to be played without search (CSSLab/maia-chess README).
NODES = 1
#: Levels of the Maia 1 weights, maia-1100 to maia-1900.
LEVELS = tuple(range(1100, 2000, 100))


class Engines:
    """The lc0 processes, by Elo level."""

    def __init__(self, lc0: str, weights_dir: Path) -> None:
        self.lc0 = lc0
        self.weights_dir = weights_dir
        self._open: dict[int, chess.engine.SimpleEngine] = {}

    def weights(self, elo: int) -> Path:
        return self.weights_dir / f"maia-{elo}.pb.gz"

    def _engine(self, elo: int) -> chess.engine.SimpleEngine:
        engine = self._open.get(elo)
        if engine is None:
            engine = chess.engine.SimpleEngine.popen_uci(
                [self.lc0, f"--weights={self.weights(elo)}"], timeout=60
            )
            self._open[elo] = engine
        return engine

    def move(self, elo: int, fen: str) -> str | None:
        """Maia's move at ``elo`` in ``fen``, in UCI ; ``None`` if lc0 fails."""
        board = chess.Board(fen)
        try:
            result = self._engine(elo).play(board, chess.engine.Limit(nodes=NODES))
        except (
            chess.engine.EngineError,
            chess.engine.EngineTerminatedError,
            OSError,
            TimeoutError,
        ) as exc:
            log.warning("lc0 maia-%s failed : %s", elo, exc.__class__.__name__)
            self.close(elo)
            return None
        return result.move.uci() if result.move is not None else None

    def close(self, elo: int | None = None) -> None:
        for level in [elo] if elo is not None else list(self._open):
            engine = self._open.pop(level, None)
            if engine is not None:
                try:
                    engine.quit()
                except Exception:  # an engine already gone
                    pass
