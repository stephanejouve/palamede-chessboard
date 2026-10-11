"""Chess rules helpers — strict SAN validation.

Split out of :mod:`palamede_chessboard.chessboard_state` in BT-001
commit 2 to keep the state module under 500 lines. Both symbols are
re-exported from ``chessboard_state`` for backward compat.

Rationale : ``python-chess.Board.parse_san`` accepts SAN in tolerant
mode (``Bxb6`` on an empty ``b6`` parses as ``Bb6``, masking a player
hallucination of the position — partie 004 round 16 incident). This
module enforces symbol/state coherence (``x`` ↔ ``is_capture``,
``+`` ↔ ``gives_check``, ``#`` ↔ ``is_checkmate``) BEFORE any push.

BT-003 (palamede#8) will promote this module to a full
``chess_arbiter`` with the move-request context helper and strict
turn-ownership enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of :func:`validate_san_strict`."""

    ok: bool
    reason: str = ""


def validate_san_strict(board: chess.Board, san: str) -> ValidationResult:
    """Return ``ok=True`` iff ``san`` parses on ``board`` AND its suffix
    symbols match the actual move state.

    Rejected on :

    - ``chess.IllegalMoveError`` / ``chess.InvalidMoveError`` /
      ``chess.AmbiguousMoveError`` from ``parse_san``.
    - ``'x' in san`` but ``board.is_capture(move) is False`` (ghost capture).
    - ``'+' in san`` but ``board.gives_check(move) is False``.
    - ``'#' in san`` but the resulting position isn't checkmate.
    - Missing ``'+'`` or ``'#'`` when the move actually delivers check /
      checkmate — the SAN is still ambiguous and we require the player to
      announce what they see.

    The board is not mutated.
    """
    try:
        move = board.parse_san(san)
    except (
        chess.IllegalMoveError,
        chess.InvalidMoveError,
        chess.AmbiguousMoveError,
    ) as exc:
        return ValidationResult(False, f"illegal SAN: {exc}")

    has_x = "x" in san
    has_plus = "+" in san
    has_hash = "#" in san

    is_capture = board.is_capture(move)
    if has_x != is_capture:
        target = chess.square_name(move.to_square)
        return ValidationResult(
            False,
            (
                f"'x' annoncé sans capture réelle (case {target} vide ou même couleur)"
                if has_x
                else f"capture réelle sur {target} sans 'x' dans le SAN"
            ),
        )

    gives_check = board.gives_check(move)

    board.push(move)
    try:
        is_mate = board.is_checkmate()
    finally:
        board.pop()

    if has_hash != is_mate:
        return ValidationResult(
            False,
            (
                "'#' annoncé sans échec et mat réel"
                if has_hash
                else "échec et mat réel sans '#' dans le SAN"
            ),
        )
    if has_plus != gives_check and not has_hash:
        return ValidationResult(
            False,
            ("'+' annoncé sans échec réel" if has_plus else "échec réel sans '+' dans le SAN"),
        )

    return ValidationResult(True)
