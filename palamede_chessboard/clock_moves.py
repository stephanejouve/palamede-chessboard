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
* **Fischer** afterwards : :meth:`ClockState.on_move_played`, the flag
  checked before the increment (#46).
* **Flag fall at reading time**, Tablier games only : :func:`settle_flag`
  ends the game on time when the side to move has run out, without waiting
  for a move that may never come. Called by ``GET /api/clock`` and
  ``chess_clock_state`` ; a spectator's read never ends an agents' game.
* **One lock per game** (:func:`game_lock`), taken by a move and by
  :func:`settle_flag` : a read that ends the game on time and a move that
  cross must not lose one another (review Leader #136).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

from palamede_chessboard import _atomic
from palamede_chessboard import chessboard_clock as clock_module
from palamede_chessboard.chessboard_clock import (
    ClockState,
    load_clock,
    result_from_flag_fall,
    save_clock,
)
from palamede_chessboard.chessboard_state import GameNotFoundError, GameState

#: Ids of the games opened from Tablier (#126), reserved since #131.
TABLIER = "tablier."


@contextlib.contextmanager
def game_lock(game_id: str, games_dir: Path) -> Iterator[None]:
    """Exclusive ``flock`` on ``<games dir>/<game_id>.pgn.lock``."""
    with _atomic.locked(games_dir / f"{game_id}.pgn"):
        yield


def _end_on_time(state: GameState, clock: ClockState) -> None:
    if clock.flag_fall is not None and state.metadata.result == "*":
        state.metadata.result = result_from_flag_fall(clock.flag_fall)
        state.save()


def clock_after_move(state: GameState, mover: str, games_dir: Path) -> ClockState | None:
    """Advance the clock of ``state`` for the move just played by ``mover``.

    ``mover`` is ``"white"`` or ``"black"``, the side that was to move
    before the move. ``None`` when the game has no clock.
    """
    clock = load_clock(state.metadata.game_id, games_dir=games_dir)
    if clock is None:
        return None
    if not clock.started:
        if not state.metadata.game_id.startswith(TABLIER):
            return clock  # the arbiter starts it (chess_clock_start)
        clock.started = True
        clock.last_move_ts = clock_module._now()
        clock.active = "black" if mover == "white" else "white"
    else:
        clock.on_move_played(mover=mover)
        _end_on_time(state, clock)
    if state.metadata.result != "*" or state.board.is_game_over():
        clock.active = None  # game over : the clock stops
    save_clock(clock, games_dir=games_dir)
    return clock


def settle_flag(game_id: str, games_dir: Path) -> ClockState | None:
    """The clock of ``game_id``, with a fallen flag applied if time ran out.

    Nothing changes for a game other than ``tablier.*``, without clock, a
    clock not started, a flag already down, or a game already over.
    """
    if not game_id.startswith(TABLIER):
        return load_clock(game_id, games_dir=games_dir)
    with game_lock(game_id, games_dir):
        return _settle_flag_locked(game_id, games_dir)


def _settle_flag_locked(game_id: str, games_dir: Path) -> ClockState | None:
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
    clock.flag_fall = clock.active
    setattr(clock, f"{clock.active}_seconds", 0.0)
    save_clock(clock, games_dir=games_dir)
    _end_on_time(state, clock)
    return clock
