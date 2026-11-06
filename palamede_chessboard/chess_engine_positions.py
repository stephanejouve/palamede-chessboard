"""The positions the engine guard compares a call to (#247).

Split out of :mod:`palamede_chessboard.chess_engine_guard`, which keeps the
decisions and the audit journal. Here : the FEN fingerprints (piece placement
and side to move, plus the mirror), and the positions the live games reached,
the opening of a game from the initial position left out.
"""

from __future__ import annotations

import logging
from pathlib import Path

import chess

from palamede_chessboard.chessboard_state import GameNotFoundError, GameState, list_games
from palamede_chessboard.paths import EXERCISE_PREFIX

log = logging.getLogger(__name__)

#: Opening threshold. Positions reached in the first N half-moves of an
#: in-progress game are excluded from the match set. CD 2026-10-03 :
#: without this threshold, every Sicilian post-mortem would be blocked
#: because the first dozen plies are shared across all our games.
OPENING_PLIES_IGNORED: int = 12


def normalize_fen_for_match(fen: str) -> str | None:
    """Return a short FEN fingerprint (piece placement + side to move).

    Clock counters (halfmove, fullmove) and castling / en-passant rights
    are dropped because a player can trivially tweak them while keeping
    the same position on the board. The fingerprint is what the guard
    compares against the in-progress position set.

    Invalid FENs return ``None`` — the caller treats that as "no match",
    i.e. the engine call stays allowed on malformed input (Stockfish will
    reject the FEN itself).
    """
    try:
        parts = fen.strip().split()
        if len(parts) < 2:
            return None
        placement, side = parts[0], parts[1]
        if side not in ("w", "b"):
            return None
        # Validate placement via python-chess so we don't match on gibberish.
        chess.Board(f"{placement} {side} - - 0 1")
        return f"{placement} {side}"
    except (ValueError, IndexError):
        return None


def _mirror_fingerprint(fingerprint: str) -> str | None:
    """Return the color-flipped, board-rotated fingerprint.

    Catches a trivial evasion where the caller feeds the guard the mirror
    of an in-progress position : same tactics, same evaluation, flipped
    board. ``chess.Board.mirror`` handles both color flip and vertical
    rotation ; we then re-normalize.
    """
    try:
        board = chess.Board(f"{fingerprint} - - 0 1")
    except ValueError:
        return None
    return normalize_fen_for_match(board.mirror().fen())


def _fingerprint_pair(fen: str) -> set[str]:
    """Return ``{fingerprint, mirror}`` for ``fen`` (empty on invalid input)."""
    out: set[str] = set()
    base = normalize_fen_for_match(fen)
    if base is None:
        return out
    out.add(base)
    mirror = _mirror_fingerprint(base)
    if mirror is not None:
        out.add(mirror)
    return out


def is_exercise_game(game_id: object) -> bool:
    """``exo.*``, whatever its case : an exercise of the coach (#95)."""
    return str(game_id or "").lower().startswith(EXERCISE_PREFIX)


def _starts_from_a_position(state: GameState) -> bool:
    """No opening to ignore : an exercise, or a game set up from a FEN."""
    return is_exercise_game(state.metadata.game_id) or (
        state.board.root().fen() != chess.STARTING_FEN
    )


def _game_position_fingerprints(state: GameState, *, whole: bool = False) -> set[str]:
    """Return the set of fingerprints (incl. mirrors) the guard compares to.

    For a game from the initial position, only positions reached from
    ply :data:`OPENING_PLIES_IGNORED` onwards ; for a game that starts
    from a position, or with ``whole``, every position, its starting one
    included. For each position we also include its mirrored fingerprint
    so a flipped-board attempt still matches. Malformed PGN bails out
    silently and leaves whatever was already collected intact.
    """
    whole = whole or _starts_from_a_position(state)
    fingerprints: set[str] = set()
    board = state.board.root()
    if whole:
        fingerprints.update(_fingerprint_pair(board.fen()))
    for san in state.san_history:
        try:
            move = board.parse_san(san)
        except ValueError:
            log.warning("engine_guard: cannot replay %s in %s", san, state.metadata.game_id)
            break
        board.push(move)
        if not whole and board.ply() < OPENING_PLIES_IGNORED:
            continue
        fingerprints.update(_fingerprint_pair(board.fen()))
    # Also include ``state.board.fen()`` directly in case the replay bailed
    # out before reaching the current position.
    if whole or state.board.ply() >= OPENING_PLIES_IGNORED:
        fingerprints.update(_fingerprint_pair(state.board.fen()))
    return fingerprints


def _live_games(games_dir: Path) -> list[GameState]:
    states = []
    for summary in list_games(games_dir, active_only=True):
        try:
            states.append(GameState.load(summary["game_id"], games_dir=games_dir))
        except GameNotFoundError:
            continue
    return states


def _in_progress_positions(games_dir: Path) -> dict[str, set[str]]:
    """Map each in-progress ``game_id`` to the fingerprints the guard compares to.

    The opening of a game from the initial position is left out here, at
    the source : any position, of any ply, is compared to these sets.
    """
    return {
        state.metadata.game_id: _game_position_fingerprints(state)
        for state in _live_games(games_dir)
    }


def game_in_play(state: GameState, games_dir: Path) -> str | None:
    """The live game that reached a position of ``state`` : checked before
    the engine, as :func:`mask_walk_entries` checks after it (#95)."""
    in_progress = _in_progress_positions(games_dir)
    board = state.board.root()
    fens = [board.fen()]
    for move in state.board.move_stack:
        board.push(move)
        fens.append(board.fen())
    matches = (_match_against_in_progress(fen, in_progress) for fen in fens)
    return next((m for m in matches if m is not None), None)


def live_game_through(fen: str, games_dir: Path) -> str | None:
    """The live game, not an exercise, that went through ``fen`` (or its mirror).

    Every position counts, the opening too : an exercise of the coach is
    an oracle for its solution, so it is refused while a game that
    reached its position is still played (review Admin #204). The
    exercise games themselves are left out, or one would refuse itself.
    """
    live = {
        state.metadata.game_id: _game_position_fingerprints(state, whole=True)
        for state in _live_games(games_dir)
        if not is_exercise_game(state.metadata.game_id)
    }
    return _match_against_in_progress(fen, live)


def _match_against_in_progress(fen: str, in_progress: dict[str, set[str]]) -> str | None:
    """Return the first in-progress ``game_id`` whose fingerprint set contains ``fen``.

    Both the FEN's direct fingerprint and its mirror are checked. Returns
    ``None`` if no in-progress game matches.
    """
    needles = _fingerprint_pair(fen)
    if not needles:
        return None
    for game_id, fingerprints in in_progress.items():
        if needles & fingerprints:
            return game_id
    return None


def _position_fen_at_ply(state: GameState, ply: int) -> str | None:
    """Replay ``state.san_history`` up to ``ply`` and return the resulting FEN.

    ``ply`` is a half-move count (0 = initial position). Returns ``None``
    if the history is too short or contains unreplayable SAN.
    """
    if ply < 0 or ply > len(state.san_history):
        return None
    board = state.board.root()
    for san in state.san_history[:ply]:
        try:
            board.push_san(san)
        except ValueError:
            return None
    return board.fen()
