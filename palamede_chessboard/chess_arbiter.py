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

import re
from dataclasses import dataclass
from typing import TypedDict

import chess


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of :func:`validate_san_strict`.

    ``kind`` names the refusal for the reader's text (#332, review Leader
    #340) : ``illegal`` (no such move), ``ambiguous``, then the notation of
    a **legal** move, ``capture`` / ``no_capture`` (``x``), ``check`` /
    ``no_check`` (``+``), ``mate`` / ``no_mate`` (``#``). ``suggestion`` is
    the SAN to write instead : the canonical one for a notation slip, the
    disambiguated candidates (``Nbd2, Nfd2``) for an ambiguous move.
    """

    ok: bool
    reason: str = ""
    kind: str = ""
    suggestion: str = ""


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
    except chess.AmbiguousMoveError as exc:
        return ValidationResult(False, f"illegal SAN: {exc}", "ambiguous", _candidates(board, san))
    except (chess.IllegalMoveError, chess.InvalidMoveError) as exc:
        return ValidationResult(False, f"illegal SAN: {exc}", "illegal")
    canonical = board.san(move)

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
            "capture" if has_x else "no_capture",
            canonical,
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
            "mate" if has_hash else "no_mate",
            canonical,
        )
    if has_plus != gives_check and not has_hash:
        # '#' implies '+' in SAN convention ; only enforce '+' when there's
        # no '#'.
        return ValidationResult(
            False,
            ("'+' annoncé sans échec réel" if has_plus else "échec réel sans '+' dans le SAN"),
            "check" if has_plus else "no_check",
            canonical,
        )

    return ValidationResult(True)


#: A piece move in SAN : the piece, an optional departure (file, rank or both),
#: an optional ``x``, the target square, an optional ``+`` / ``#``.
_PIECE_SAN = re.compile(r"^([KQRBN])[a-h]?[1-8]?x?([a-h][1-8])[+#]?$")


def _candidates(board: chess.Board, san: str) -> str:
    """The disambiguated SANs an ambiguous ``san`` could mean (``Nbd2, Nfd2``).

    Every legal move of the same piece to the same target square, written the
    board's canonical way. The departure the player wrote so far is ignored :
    ``Nbd2`` with knights on b1 and b3 is still ambiguous, and its candidates
    are ``N1d2`` and ``N3d2`` (reviews Leader and Admin #340).
    """
    match = _PIECE_SAN.match(san)
    if match is None:
        return ""
    piece_type = chess.PIECE_SYMBOLS.index(match[1].lower())
    target = chess.parse_square(match[2])
    return ", ".join(
        board.san(move)
        for move in board.legal_moves
        if move.to_square == target and board.piece_type_at(move.from_square) == piece_type
    )


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
    if last_n <= 0 or not board.move_stack:
        last_moves: list[str] = []
    else:
        history = list(board.move_stack)
        take = history[-last_n:]
        # Index in the move stack, not ``ply`` : ``board.ply()`` is
        # absolute and does not start at 0 from a ``starting_fen``.
        skip = len(history) - len(take)
        replay = board.root()
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
