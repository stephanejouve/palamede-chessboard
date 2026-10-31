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
  ``chess_clock_state``. Only a player of the game or its arbiter may do it
  (owner, 31/10 ; FIDE 6.8 : the arbiter observes it or a player claims
  it) : anyone else's read shows the clock and changes nothing, and a
  spectator's read never ends an agents' game.
* **Out of time, but no mating material left** (FIDE 6.9) : the game is
  drawn, not lost, when the opponent of the side whose flag fell cannot
  mate by any series of legal moves (:func:`result_on_time`).
* **One lock per game** (:func:`game_lock`), taken by a move and by
  :func:`settle_flag` : a read that ends the game on time and a move that
  cross must not lose one another (review Leader #136).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import chess

from palamede_chessboard import _atomic
from palamede_chessboard import chessboard_clock as clock_module
from palamede_chessboard.chessboard_clock import (
    ClockState,
    load_clock,
    result_from_flag_fall,
    save_clock,
)
from palamede_chessboard.chessboard_state import GameNotFoundError, GameState
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
        raise GameNotFoundError(str(path))
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


def result_on_time(state: GameState, flag_fall: str) -> str:
    """The result when ``flag_fall``'s side runs out of time (FIDE 6.9).

    A loss, unless the opponent cannot mate by any series of legal moves :
    then a draw. Example : White, king and queen, runs out against a lone
    king and bishop → ``1/2-1/2``, not ``0-1``.
    """
    opponent = chess.BLACK if flag_fall == "white" else chess.WHITE
    if state.board.has_insufficient_material(opponent):
        return "1/2-1/2"
    return result_from_flag_fall(flag_fall)


def _end_on_time(state: GameState, clock: ClockState) -> None:
    if clock.flag_fall is not None and state.metadata.result == "*":
        state.metadata.result = result_on_time(state, clock.flag_fall)
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
        return load_clock(game_id, games_dir=games_dir)
    try:
        with game_lock(game_id, games_dir):
            return _settle_flag_locked(game_id, games_dir, headers)
    except GameNotFoundError:
        return None


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
