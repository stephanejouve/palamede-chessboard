"""Clock state for ``mcp_chessboard_server`` games (Phase A).

Adds an optional Fischer-style chess clock to :mod:`chessboard_state`. The
clock is a thin wrapper around two countdowns (white / black) plus an
``active`` cursor that flips after every successful :py:meth:`GameState.play_move`.

* Initialisation parses :class:`TimeControl` ``format`` strings of the
  form ``"M+S"`` (minutes base + per-move increment in seconds).
  ``unrated`` and unparseable values disable the clock.
* On each move the active player loses ``now - last_move_ts`` seconds
  then gains ``increment_seconds``. If they would go negative, the
  result tag flips to ``"OUT_OF_TIME"`` and the move is *still* recorded
  (the engine refuses no legal SAN — flag-fall is decided after).
* Persistence is a JSONL side-car ``clock_<game_id>.json`` (single-line
  snapshot, rewritten on every change) next to the PGN.

The clock module is independent of :class:`GameState` ; the MCP server
wires the two together so ``chess_play_move`` updates both atomically.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from palamede_chessboard.chessboard_state import GAMES_DIR, TimeControl, _validate_game_id

CLOCK_FORMAT_RE = re.compile(r"^\s*(\d+)\s*\+\s*(\d+)\s*$")
OUT_OF_TIME_RESULT_WHITE_LOSES = "0-1"
OUT_OF_TIME_RESULT_BLACK_LOSES = "1-0"


def _now() -> float:
    """Current wall-clock seconds (UTC). Mockable via monkeypatch."""
    return datetime.now(UTC).timestamp()


def parse_time_control(tc: TimeControl) -> tuple[int, int] | None:
    """Return ``(start_seconds, increment_seconds)`` or ``None`` if disabled.

    Priority : explicit ``start_seconds`` / ``increment_seconds`` fields,
    then ``format`` string ``"M+S"`` (minutes + seconds). ``unrated``
    disables the clock.
    """
    if tc.format == "unrated" and tc.start_seconds is None:
        return None
    if tc.start_seconds is not None:
        return (
            int(tc.start_seconds),
            int(tc.increment_seconds or 0),
        )
    match = CLOCK_FORMAT_RE.match(tc.format)
    if not match:
        return None
    minutes, increment = match.groups()
    return (int(minutes) * 60, int(increment))


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

    @classmethod
    def init_from_time_control(cls, game_id: str, tc: TimeControl) -> ClockState | None:
        """Build a fresh clock from a :class:`TimeControl`, or ``None``."""
        parsed = parse_time_control(tc)
        if parsed is None:
            return None
        start, increment = parsed
        return cls(
            game_id=game_id,
            white_seconds=float(start),
            black_seconds=float(start),
            increment_seconds=increment,
        )

    def start(self, *, now: float | None = None) -> ClockState:
        """Begin counting against ``white`` from ``now``."""
        self.active = "white"
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
        callers can read it to translate into a result tag.
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
            self.white_seconds += self.increment_seconds
            self.active = "black"
        elif mover == "black":
            self.black_seconds -= delta
            if self.black_seconds <= 0:
                self.flag_fall = "black"
            self.black_seconds += self.increment_seconds
            self.active = "white"
        else:
            raise ValueError(f"Unknown mover: {mover!r}")
        self.last_move_ts = ts
        return self

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
    tmp.replace(path)
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
    return ClockState(**data)


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
