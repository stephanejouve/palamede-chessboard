"""Clock state for ``mcp_chessboard_server`` games (Phase A).

Adds an optional Fischer-style chess clock to :mod:`chessboard_state`. The
clock is a thin wrapper around two countdowns (white / black) plus an
``active`` cursor that flips after every successful :py:meth:`GameState.play_move`.

* Initialisation parses :class:`TimeControl` ``format`` strings of the
  form ``"M+S"`` (minutes base + per-move increment in seconds).
  ``unrated`` and unparseable values disable the clock.
* Each side starts with ``start + increment`` : the Fischer increment is
  added before the first move (FIDE C.02.03, 3.3.3 ; #160), so a 90+30
  clock reads 90:30 at the start.
* On each move the active player loses ``now - last_move_ts`` seconds
  then gains ``increment_seconds``. If they would go negative, the
  result tag flips to ``"OUT_OF_TIME"`` and the move is *still* recorded
  (the engine refuses no legal SAN — flag-fall is decided after).
* **Relay hold** (#164) : the side to move that plays on an eBoard
  (:attr:`ClockState.board_sides`, declared by its bridge) must first set the
  opponent's move on the board. Its time is held until its bridge says the
  board matches the game (:meth:`ClockState.release`), at most
  :data:`HOLD_SECONDS` : ``last_move_ts`` is moved that far ahead, and every
  reader already counts ``max(0, now - last_move_ts)``. A side that moves
  while still held has a silent bridge, a Tablier killed without closing :
  it loses the hold (:meth:`ClockState.drop_silent`) until its bridge says
  again that the board matches (#164a).
* Persistence is a JSONL side-car ``clock_<game_id>.json`` (single-line
  snapshot, rewritten on every change) next to the PGN.

The clock module is independent of :class:`GameState` ; the MCP server
wires the two together so ``chess_play_move`` updates both atomically.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path

from palamede_chessboard._atomic import fsync_dir
from palamede_chessboard.chessboard_state import GAMES_DIR, TimeControl, _validate_game_id
from palamede_chessboard.metadata import parse_time_control

OUT_OF_TIME_RESULT_WHITE_LOSES = "0-1"
OUT_OF_TIME_RESULT_BLACK_LOSES = "1-0"
#: The longest a relay hold lasts (#164, owner 01/11) : a silent bridge, or a
#: board never set, cannot freeze a clock.
HOLD_SECONDS = 20.0


def _now() -> float:
    """Current wall-clock seconds (UTC). Mockable via monkeypatch."""
    return datetime.now(UTC).timestamp()


@dataclass
class ClockState:
    """In-memory clock state for a single game."""

    game_id: str
    white_seconds: float
    black_seconds: float
    increment_seconds: int
    active: str | None = None  # "white" / "black" / None when paused
    last_move_ts: float | None = None  # UTC seconds since epoch
    started: bool = False
    flag_fall: str | None = None  # "white" / "black" when out of time
    #: The sides played on an eBoard (#164), as their bridges declare.
    board_sides: list[str] = field(default_factory=list)
    #: The side to move's time is held until its board matches (#164).
    held: bool = False
    #: The sides dropped from :attr:`board_sides` for a silent bridge (#164a).
    stale_sides: list[str] = field(default_factory=list)
    #: Who switched the clock off before the first move (#316) : the game is
    #: played without it, and the opponent reads who decided it.
    off_by: str | None = None

    @classmethod
    def init_from_time_control(cls, game_id: str, tc: TimeControl) -> ClockState | None:
        """Build a fresh clock from a :class:`TimeControl`, or ``None``.

        Both sides get the increment before the first move (FIDE C.02.03,
        3.3.3 ; #160). Clocks already saved keep their values.
        """
        parsed = parse_time_control(tc)
        if parsed is None:
            return None
        start, increment = parsed
        return cls(
            game_id=game_id,
            white_seconds=float(start + increment),
            black_seconds=float(start + increment),
            increment_seconds=increment,
        )

    def start(self, *, side: str = "white", now: float | None = None) -> ClockState:
        """Begin counting against ``side``, the side to move, from ``now``."""
        self.active = side
        self.last_move_ts = now if now is not None else _now()
        self.started = True
        return self

    def on_move_played(
        self,
        *,
        mover: str,
        now: float | None = None,
        relay_floor: float = 0.0,
    ) -> ClockState:
        """Decrement ``mover``'s clock then apply Fischer increment.

        ``mover`` is ``"white"`` or ``"black"``. The clock must have been
        :py:meth:`start`-ed beforehand ; otherwise this is a no-op.

        ``relay_floor`` (default ``0``) discounts the first ``N`` seconds
        of wall-clock drift from the effective think time — useful in
        relay-mediated arbitrage (e.g. Talk-routed chess) where the time
        between a player's actual move and the arbiter's tick includes
        unavoidable polling latency. With ``relay_floor=30``, a move
        that took ≤30s of wall-time consumes 0s of clock ; a 45s
        wall-move consumes 15s of clock.

        Sets :attr:`flag_fall` if the mover's clock goes negative ;
        callers can read it to translate into a result tag. A move made
        after the flag fell earns **no** increment (CD 57603, #46) : the
        flag is checked before the increment, which must stay that way.
        """
        if not self.started or self.last_move_ts is None:
            return self
        ts = now if now is not None else _now()
        raw_delta = max(0.0, ts - self.last_move_ts)
        delta = max(0.0, raw_delta - max(0.0, relay_floor))
        if mover == "white":
            self.white_seconds -= delta
            if self.white_seconds <= 0:
                self.flag_fall = "white"
            else:
                self.white_seconds += self.increment_seconds
            self.active = "black"
        elif mover == "black":
            self.black_seconds -= delta
            if self.black_seconds <= 0:
                self.flag_fall = "black"
            else:
                self.black_seconds += self.increment_seconds
            self.active = "white"
        else:
            raise ValueError(f"Unknown mover: {mover!r}")
        self.last_move_ts = ts
        return self

    def hold_for_relay(self) -> None:
        """After a move : hold the side to move's time if it plays on an eBoard (#164)."""
        self.held = (
            self.active in self.board_sides
            and self.last_move_ts is not None
            and self.flag_fall is None
        )
        if self.held and self.last_move_ts is not None:
            self.last_move_ts += HOLD_SECONDS

    def drop_silent(self, mover: str) -> bool:
        """``mover`` moves while still held : its bridge never said the board matched.

        A Tablier killed without closing leaves its side declared (review
        Junior and Admin #166) : it would win the hold at every move. The side
        leaves :attr:`board_sides` for :attr:`stale_sides`, until its bridge
        speaks again (:meth:`rejoin`). ``True`` when dropped (#164a).
        """
        if not (self.held and self.active == mover and mover in self.board_sides):
            return False
        self.board_sides = [side for side in self.board_sides if side != mover]
        self.stale_sides = sorted(set(self.stale_sides) | {mover})
        return True

    def rejoin(self, side: str) -> bool:
        """The bridge of a dropped ``side`` says its board matches : it plays on it (#164a)."""
        if side not in self.stale_sides:
            return False
        self.stale_sides = [s for s in self.stale_sides if s != side]
        self.board_sides = sorted(set(self.board_sides) | {side})
        return True

    def stop(self, *, now: float | None = None) -> bool:
        """The game is over (#287) : charge the side to move its time, then stop.

        Resignation and agreed draws end a game without a move, so nothing
        else stops the clock : ``active`` would stay set and every display
        would keep counting down. A hold (#164) has pushed ``last_move_ts``
        ahead : no time is charged before it ends. ``False`` when the clock
        was not running.
        """
        if self.active not in ("white", "black") or not self.started:
            return False
        if self.flag_fall is None and self.last_move_ts is not None:
            spent = max((now if now is not None else _now()) - self.last_move_ts, 0.0)
            left = getattr(self, f"{self.active}_seconds") - spent
            setattr(self, f"{self.active}_seconds", max(left, 0.0))
        self.active = None
        self.held = False
        return True

    def release(self, *, now: float | None = None) -> bool:
        """The board matches (#164) : the side to move's time runs from ``now``.

        ``False`` when nothing was held. Never later than the hold's end.
        """
        if not self.held or self.last_move_ts is None:
            return False
        ts = now if now is not None else _now()
        self.last_move_ts = min(self.last_move_ts, ts)
        self.held = False
        return True

    def to_dict(self) -> dict:
        snap = asdict(self)
        snap["now_ts"] = _now()
        return snap


def _clock_path(games_dir: Path, game_id: str) -> Path:
    return games_dir / f"clock_{game_id}.json"


def save_clock(state: ClockState, games_dir: Path | None = None) -> Path:
    """Write the clock snapshot to disk (rewritten on every change)."""
    _validate_game_id(state.game_id)
    games_dir = games_dir or GAMES_DIR
    games_dir.mkdir(parents=True, exist_ok=True)
    path = _clock_path(games_dir, state.game_id)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(asdict(state), f, indent=2)
        f.flush()
        os.fsync(f.fileno())  # durable before the swap (#46)
    tmp.replace(path)
    fsync_dir(path.parent)  # the rename itself (#246)
    return path


def load_clock(game_id: str, *, games_dir: Path | None = None) -> ClockState | None:
    """Return the persisted clock state or ``None`` if absent."""
    _validate_game_id(game_id)
    games_dir = games_dir or GAMES_DIR
    path = _clock_path(games_dir, game_id)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # A field this version does not know (written by a newer one) is left out :
    # a rollback must still read the clock (#164a).
    known = {fld.name for fld in fields(ClockState)}
    return ClockState(**{k: v for k, v in data.items() if k in known})


def init_and_save_clock(
    game_id: str,
    tc: TimeControl,
    *,
    games_dir: Path | None = None,
) -> ClockState | None:
    """Init a clock from ``tc`` and persist its side-car ; return the state.

    Convenience helper (CD review PR #297 bloquant 2) for callers that
    create a game AND want the clock active. ``chess_create_game`` MCP
    tool and ``chess-matchmaking create`` CLI both call it so the
    side-car ``clock_<game_id>.json`` lands on disk in one step. If
    ``tc`` disables the clock (``unrated`` or unparseable), returns
    ``None`` and writes nothing.
    """
    clock = ClockState.init_from_time_control(game_id, tc)
    if clock is None:
        return None
    save_clock(clock, games_dir=games_dir)
    return clock


def result_from_flag_fall(flag_fall: str) -> str:
    """Translate a flag-fall side to a PGN result tag."""
    if flag_fall == "white":
        return OUT_OF_TIME_RESULT_WHITE_LOSES
    if flag_fall == "black":
        return OUT_OF_TIME_RESULT_BLACK_LOSES
    raise ValueError(f"Unknown flag_fall: {flag_fall!r}")
