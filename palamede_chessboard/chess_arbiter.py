"""Chess arbiter helpers — strict SAN validation + move request context.

Rationale : ``python-chess.Board.parse_san`` accepts SAN in tolerant mode.
``Bxb6`` on an empty ``b6`` square is accepted as ``Bb6``. Same for ``+`` /
``#`` suffixes that don't match the actual check/checkmate state. This
tolerance hides player hallucinations of the position (partie 004, tour 16 :
admin annonce ``16.Bxb6`` sur cavalier absent, PGN aurait absorbé le lapsus
en simple move Bb6 sans signal — perte du fou sur ``Qxb6``).

This module exposes two helpers :

- ``validate_san_strict(board, san)`` — enforces symbol/state coherence
  (``x`` ↔ ``is_capture``, ``+`` ↔ ``gives_check``, ``#`` ↔ ``is_checkmate``)
  BEFORE any push, so an inconsistent SAN is rejected with an explicit
  reason and the caller can loop back to the player.
- ``move_request_context(board, last_n=6)`` — builds the FEN + N last
  half-moves in SAN that every arbiter turn notification MUST include, so
  the player never plays from a stale position in memory (arbitrage CD
  2026-09-26 fixed protocol after partie 004).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

import chess


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of :func:`validate_san_strict`."""

    ok: bool
    reason: str = ""


class MoveRequestContext(TypedDict):
    """Payload returned by :func:`move_request_context`.

    Typed so consumers (daemon, server) can `ctx["ply"] // 2` or
    `' '.join(ctx["last_moves"])` without mypy complaining about `object`.
    """

    fen: str
    last_moves: list[str]
    ply: int
    turn: str


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
                f"'x' annoncé sans capture réelle (case {target} " f"vide ou même couleur)"
                if has_x
                else f"capture réelle sur {target} sans 'x' dans le SAN"
            ),
        )

    gives_check = board.gives_check(move)

    # is_checkmate needs a temporary push to inspect the resulting position.
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
        # '#' implies '+' in SAN convention ; only enforce '+' when there's
        # no '#'.
        return ValidationResult(
            False,
            ("'+' annoncé sans échec réel" if has_plus else "échec réel sans '+' dans le SAN"),
        )

    return ValidationResult(True)


def move_request_context(board: chess.Board, last_n: int = 6) -> MoveRequestContext:
    """Return the payload that MUST accompany every arbiter turn request.

    Keys :

    - ``fen`` : the position the player must play from
    - ``last_moves`` : the ``last_n`` most recent half-moves in SAN (list),
      oldest first
    - ``ply`` : current half-move count (0 = initial position)
    - ``turn`` : ``"white"`` / ``"black"``

    The FEN is authoritative — the SAN list is context for humans and for
    LLM sanity checks (spot mid-play desyncs).
    """
    ply = board.ply()
    if last_n <= 0 or ply == 0:
        last_moves: list[str] = []
    else:
        history = list(board.move_stack)
        take = history[-last_n:]
        skip = ply - len(take)
        replay = chess.Board()
        for m in history[:skip]:
            replay.push(m)
        last_moves = []
        for m in take:
            last_moves.append(replay.san(m))
            replay.push(m)
    return {
        "fen": board.fen(),
        "last_moves": last_moves,
        "ply": ply,
        "turn": "white" if board.turn == chess.WHITE else "black",
    }
