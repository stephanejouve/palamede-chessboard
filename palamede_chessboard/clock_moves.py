"""The clock of a game, on every path a move can take (#135).

One place for what a played move does to the clock, shared by the MCP tool
``chess_play_move`` and the viewer's ``POST /api/games/<id>/move`` (the
Chessnut board, the mouse, the Maia bot) : two copies would drift apart
(review Leader #135).

* **Start on the first move**, Tablier games only (owner, 29/10) : in a
  ``tablier.*`` game, a clock not started yet starts when the first move is
  played, like a club clock — the move costs nothing and the other side's
  time starts running. The agents' games (matchmaking, rated) keep the
  arbiter's ``chess_clock_start`` (review Leader #136).
* **The reply waits for the clock**, other games (owner, 10/11, #316) : the
  first move may be played with the clock not started, the reply is
  refused until a player starts it (:func:`start_clock`, the click on the
  viewer's clock, or ``chess_clock_start``). Unless a player switched it
  off before the first move (:func:`switch_clock_off`) : the game is then
  played without it, and ``off_by`` tells the opponent who decided it.
* **Fischer** afterwards : :meth:`ClockState.on_move_played`, the flag
  checked before the increment (#46).
* **Flag fall at reading time**, Tablier games only : :func:`settle_flag`
  ends the game on time when the side to move has run out, without waiting
  for a move that may never come. Called by ``GET /api/clock`` and
  ``chess_clock_state``. Only a player of the game or its arbiter may do it
  (owner, 31/10 ; FIDE 6.8 : the arbiter observes it or a player claims
  it) : anyone else's read shows the clock and changes nothing, and a
  spectator's read never ends an agents' game.
* **Out of time, but no mating material left** (FIDE 6.9) : the game is
  drawn, not lost, when the opponent of the side whose flag fell cannot
  mate by any series of legal moves (:func:`result_on_time`).
* **Relay hold** (#164) : after each move, the side to move that plays on
  an eBoard has its time held until its board matches, see
  :meth:`ClockState.hold_for_relay` and :func:`release_on_sync`.
* **One lock per game** (:func:`game_lock`), taken by a move, a
  resignation, an agreed draw and :func:`settle_flag` : a read that ends
  the game on time and a move or an end that cross must not lose one
  another (review Leader #136, #289).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import chess
import chess.pgn

from palamede_chessboard import _atomic
from palamede_chessboard import chessboard_clock as clock_module
from palamede_chessboard.chessboard_clock import (
    ClockState,
    load_clock,
    result_from_flag_fall,
    save_clock,
)
from palamede_chessboard.chessboard_state import (
    GameNotFoundError,
    GameState,
    InvalidGameStateError,
)
from palamede_chessboard.paths import _pgn_path, _validate_game_id
from palamede_chessboard.players.observe import authorize_move

#: Ids of the games opened from Tablier (#126), reserved since #131.
TABLIER = "tablier."


def check_game(game_id: str, games_dir: Path) -> Path:
    """The game's PGN path ; :class:`ChessboardError` if the id is malformed,
    :class:`GameNotFoundError` if the game does not exist. Creates nothing."""
    _validate_game_id(game_id)
    path = _pgn_path(games_dir, game_id)
    if not path.exists():
        raise GameNotFoundError(f"game not found: {game_id}")  # never the path (#107)
    return path


@contextlib.contextmanager
def game_lock(game_id: str, games_dir: Path) -> Iterator[None]:
    """Exclusive ``flock`` on ``<games dir>/<game_id>.pgn.lock``.

    The id is checked and the game must exist **before** anything is
    created : an anonymous request on a made-up or malformed id leaves no
    ``.lock`` file behind (review Leader #136). :class:`ChessboardError`
    for a malformed id, :class:`GameNotFoundError` for a missing game.
    """
    with _atomic.locked(check_game(game_id, games_dir)):
        yield


def result_on_time(board: chess.Board, flag_fall: str) -> str:
    """The result when ``flag_fall``'s side runs out of time (FIDE 6.9).

    A loss, unless the opponent cannot mate by any series of legal moves
    from ``board``, the position when the flag fell : then a draw. Example :
    White, king and queen, runs out against a lone king and bishop →
    ``1/2-1/2``, not ``0-1``.
    """
    opponent = chess.BLACK if flag_fall == "white" else chess.WHITE
    if board.has_insufficient_material(opponent):
        return "1/2-1/2"
    return result_from_flag_fall(flag_fall)


def _end_on_time(state: GameState, clock: ClockState, board: chess.Board | None = None) -> None:
    """``board`` : the position when the flag fell, ``state.board`` by default."""
    if clock.flag_fall is not None and state.metadata.result == "*":
        state.metadata.result = result_on_time(board or state.board, clock.flag_fall)
        state.metadata.termination = "time forfeit"
        state.save()


def clock_after_move(state: GameState, mover: str, games_dir: Path) -> ClockState | None:
    """Advance the clock of ``state`` for the move just played by ``mover``.

    ``mover`` is ``"white"`` or ``"black"``, the side that was to move
    before the move. ``None`` when the game has no clock.
    """
    clock = load_clock(state.metadata.game_id, games_dir=games_dir)
    if clock is None or clock.off_by is not None:
        return clock  # switched off (#316) : no time, no [%clk]
    if not clock.started:
        if not state.metadata.game_id.startswith(TABLIER):
            return clock  # the arbiter starts it (chess_clock_start)
        clock.started = True
        clock.last_move_ts = clock_module._now()
        clock.active = "black" if mover == "white" else "white"
    else:
        clock.drop_silent(mover)  # still held : its bridge is gone (#164a)
        clock.on_move_played(mover=mover)
        # The flag fell before the late move : 6.9 is judged on the position
        # it was played from, or taking the last pawn would turn a loss into a
        # draw (review Leader #157).
        before = state.board.copy()
        before.pop()
        _end_on_time(state, clock, before)
    if state.metadata.result != "*" or state.board.is_game_over():
        clock.active = None  # game over : the clock stops
    clock.hold_for_relay()
    save_clock(clock, games_dir=games_dir)
    # The mover's time left, written after the move as [%clk] (#151).
    state.clocks[len(state.san_history) - 1] = getattr(clock, f"{mover}_seconds")
    state.save()
    return clock


def check_reply_allowed(state: GameState, games_dir: Path) -> None:
    """The move about to be played on ``state`` may be (#316), under the game lock.

    :class:`InvalidGameStateError` when a move was already played and the
    clock is neither started nor switched off. ``tablier.*`` games start it
    on the first move : never refused.
    """
    if not state.san_history or state.metadata.game_id.startswith(TABLIER):
        return
    clock = load_clock(state.metadata.game_id, games_dir=games_dir)
    if clock is not None and not clock.started and clock.off_by is None:
        raise InvalidGameStateError("Pendule non démarrée : un joueur la lance avant la réponse")


def _clock_to_set(state: GameState, games_dir: Path) -> ClockState:
    """The clock of a live game, for a start or a switch-off (#316)."""
    if state.metadata.result != "*" or state.board.is_game_over():
        raise InvalidGameStateError("La partie est terminée")
    clock = load_clock(state.metadata.game_id, games_dir=games_dir)
    if clock is None:
        raise InvalidGameStateError("Cette partie n'a pas de pendule")
    return clock


def start_clock(game_id: str, games_dir: Path) -> ClockState:
    """Start the clock of a live game, against the side to move (#316).

    Already started : left as it is, a second click restarts nothing.
    Switched off : :class:`InvalidGameStateError`, the game goes on without.
    """
    with game_lock(game_id, games_dir):
        state = GameState.load(game_id, games_dir=games_dir)
        clock = _clock_to_set(state, games_dir)
        if clock.started:
            return clock
        if clock.off_by is not None:
            raise InvalidGameStateError("Pendule coupée avant le premier coup")
        clock.start(side="white" if state.board.turn else "black")
        save_clock(clock, games_dir=games_dir)
        return clock


def switch_clock_off(game_id: str, by: str, games_dir: Path) -> ClockState:
    """``by`` plays this game without clock (owner, 10/11, #316).

    Only before the first move and with the clock not started :
    :class:`InvalidGameStateError` otherwise. Final for the game.
    """
    with game_lock(game_id, games_dir):
        state = GameState.load(game_id, games_dir=games_dir)
        clock = _clock_to_set(state, games_dir)
        if clock.off_by is not None:
            return clock
        if clock.started or state.san_history:
            raise InvalidGameStateError("La pendule se coupe avant le premier coup")
        clock.off_by = by
        save_clock(clock, games_dir=games_dir)
        return clock


def stop_clock_locked(game_id: str, games_dir: Path) -> ClockState | None:
    """Stop the clock of a game that has just ended without a move (#287).

    Called **under** :func:`game_lock`, in the same hold as the resignation
    or the agreed draw : a read that makes the flag fall cannot slip in
    between the end and the clock (#289). The side to move is charged its
    time, ``active`` is cleared. A checkmate or a stalemate is stopped by
    :func:`clock_after_move`, a flag fall by :func:`settle_flag`.
    """
    clock = load_clock(game_id, games_dir=games_dir)
    if clock is not None and clock.stop():
        save_clock(clock, games_dir=games_dir)
    return clock


def _result_header(game_id: str, games_dir: Path) -> str | None:
    """The game's ``Result`` header, read alone (#289) ; ``None`` if unreadable.

    A checkmate or a stalemate writes it on the move (``play_move``) : the
    header says whether the game is over without replaying the moves.
    """
    try:
        with check_game(game_id, games_dir).open("r", encoding="utf-8") as f:
            headers = chess.pgn.read_headers(f)
    except GameNotFoundError:
        return None
    return headers.get("Result", "*") if headers is not None else None


def _stopped_if_over(game_id: str, clock: ClockState | None, games_dir: Path) -> ClockState | None:
    """A game already over shows a stopped clock (#287), without rewriting it.

    For a game ended before :func:`stop_clock_locked` existed : its clock
    still names a side to move. Nothing is charged, the end's time being
    unknown. Only the ``Result`` header is read : the viewer asks for the
    clock every 5 s (#289).
    """
    if clock is None or clock.active is None:
        return clock
    if _result_header(game_id, games_dir) not in (None, "*"):
        clock.active = None
        clock.held = False
    return clock


def settle_flag(game_id: str, games_dir: Path, headers: Any = None) -> ClockState | None:
    """The clock of ``game_id``, with a fallen flag applied if time ran out.

    ``headers`` carry the caller's ``Authorization: Bearer`` token : only a
    player of the game or its arbiter may end it on time (owner, 31/10),
    under the same rule as ``chess_clock_start``. Anyone else, or no token,
    reads the clock unchanged. Nothing changes either for a game other than
    ``tablier.*``, without clock, a clock not started, a flag already down,
    or a game already over.
    """
    if not game_id.startswith(TABLIER):
        return _stopped_if_over(game_id, load_clock(game_id, games_dir=games_dir), games_dir)
    try:
        with game_lock(game_id, games_dir):
            clock = _settle_flag_locked(game_id, games_dir, headers)
    except GameNotFoundError:
        return None
    return _stopped_if_over(game_id, clock, games_dir)


def _settle_flag_locked(game_id: str, games_dir: Path, headers: Any) -> ClockState | None:
    clock = load_clock(game_id, games_dir=games_dir)
    if clock is None or not clock.started or clock.flag_fall is not None:
        return clock
    if clock.active not in ("white", "black") or clock.last_move_ts is None:
        return clock
    left = getattr(clock, f"{clock.active}_seconds") - (clock_module._now() - clock.last_move_ts)
    if left > 0:
        return clock
    try:
        state = GameState.load(game_id, games_dir=games_dir)
    except GameNotFoundError:
        return clock
    if state.metadata.result != "*" or state.board.is_game_over():
        return clock
    _who, refused = authorize_move(headers, state, games_dir, "chess_clock_start")
    if refused is not None:
        return clock  # a spectator, or no token : the clock is shown, nothing more
    clock.flag_fall = clock.active
    setattr(clock, f"{clock.active}_seconds", 0.0)
    save_clock(clock, games_dir=games_dir)
    _end_on_time(state, clock)
    return clock


def declare_board(game_id: str, sides: list[str], with_board: bool, games_dir: Path) -> None:
    """The bridge of ``sides`` plays on an eBoard, or no longer does (#164).

    Leaving the board ends a hold of those sides at once : nothing to set.
    A declaration left by a bridge that died expires on its own : the side
    loses the hold at its first move made while held (#164a).
    """
    with game_lock(game_id, games_dir):
        clock = load_clock(game_id, games_dir=games_dir)
        if clock is None:
            return
        others = [side for side in clock.board_sides if side not in sides]
        clock.board_sides = sorted(set(others + sides)) if with_board else others
        clock.stale_sides = [side for side in clock.stale_sides if side not in sides]
        if not with_board and clock.active in sides:
            clock.release()
        save_clock(clock, games_dir=games_dir)


def release_on_sync(game_id: str, board_fen: str, games_dir: Path) -> bool:
    """The board of the side to move shows ``board_fen`` (#164) : release its hold.

    Only for the game's current position : a late signal about an older one
    releases nothing. ``True`` when the time now runs. A side dropped for a
    silent bridge plays on its board again (#164a) : held from the next move.
    """
    with game_lock(game_id, games_dir):
        clock = load_clock(game_id, games_dir=games_dir)
        if clock is None or not (clock.held or clock.active in clock.stale_sides):
            return False
        state = GameState.load(game_id, games_dir=games_dir)
        if state.board.board_fen() != board_fen:
            return False
        if clock.active is not None:
            clock.rejoin(clock.active)
        released = clock.release()
        save_clock(clock, games_dir=games_dir)
        return released
