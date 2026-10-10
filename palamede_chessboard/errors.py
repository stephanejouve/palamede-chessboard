"""Exceptions raised by the chessboard state machine.

Split out of :mod:`palamede_chessboard.chessboard_state` in BT-001
commit 2 to keep the state module under 500 lines. All classes
remain re-exported from ``chessboard_state`` for backward compat.
"""

from __future__ import annotations


class ChessboardError(Exception):
    """Base error raised by chessboard_state."""


class IllegalMoveError(ChessboardError):
    """Raised when a SAN move is rejected by ``chess.Board.parse_san``."""


class GameNotFoundError(ChessboardError):
    """Raised when a referenced ``game_id`` has no PGN on disk."""


class GameAlreadyExistsError(ChessboardError):
    """Raised on ``create`` when the ``game_id`` PGN already exists."""


class InvalidGameStateError(ChessboardError):
    """Raised when an operation is illegal in the current game state.

    Examples: ``resign`` after the game already ended ; ``agree_draw``
    with no pending offer ; ``agree_draw`` on one's own offer.
    """


class StalePlyError(ChessboardError):
    """Raised when ``play_move(expected_ply=…)`` is given but the board
    is no longer at that ply (another player — or the same agent in a
    duplicate invocation — already pushed a move since the caller read
    the state).

    Attributes ``current_ply`` and ``expected_ply`` are exposed so the
    MCP handler can surface them in the structured error payload.
    """

    def __init__(self, current_ply: int, expected_ply: int) -> None:
        super().__init__(f"Stale ply: expected {expected_ply}, board at {current_ply}")
        self.current_ply = current_ply
        self.expected_ply = expected_ply
