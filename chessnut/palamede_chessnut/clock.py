"""The game's clock for the page (#135) : read from the viewer, shown in the window.

The bridge reads the viewer's public ``GET /api/clock`` (never with the
token), at most every :data:`REFRESH` seconds whatever the page's polling,
and gives the page what is left to each side. Reading it is also what lets
the server end a Tablier game on time when nobody moves (``settle_flag``,
#135) : the window open is enough.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from urllib.parse import quote

from palamede_chessnut import viewer

log = logging.getLogger(__name__)

#: Seconds between two reads of the viewer's clock.
REFRESH = 1.0
#: A slow viewer must not hold the page's ``/state`` for long.
TIMEOUT = 3.0


def seconds_left(clock: Any, age: float = 0.0) -> dict[str, Any] | None:
    """What the page shows, from the viewer's clock read ``age`` seconds ago.

    ``{white, black, active, flag, started}`` : the seconds left to each side,
    the side whose time runs (``None`` before the first move, after a flag
    fall or at the end of the game), the side whose flag fell. ``None`` for a
    game without clock or an answer that cannot be read.
    """
    if not isinstance(clock, dict):
        return None
    try:
        left = {"white": float(clock["white_seconds"]), "black": float(clock["black_seconds"])}
        now_ts = float(clock["now_ts"])
        last = clock.get("last_move_ts")
        last_ts = float(last) if last is not None else None
    except (KeyError, TypeError, ValueError):
        return None
    flag = clock.get("flag_fall") if clock.get("flag_fall") in ("white", "black") else None
    started = bool(clock.get("started"))
    active = clock.get("active")
    if not started or flag is not None or active not in left or last_ts is None:
        active = None
    else:
        # The time spent by the side to move : on the server, then since the read.
        left[active] -= now_ts - last_ts + age
    return {
        "white": max(left["white"], 0.0),
        "black": max(left["black"], 0.0),
        "active": active,
        "flag": flag,
        "started": started,
    }


class ClockCache:
    """The last clock read for the game on screen ; read again when stale."""

    def __init__(self, monotonic: Any = time.monotonic) -> None:
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._game_id: str | None = None
        self._clock: Any = None
        #: When the clock shown was read, and when a read was last tried : a
        #: viewer that does not answer is tried once per REFRESH, not per poll.
        self._fetched = 0.0
        self._tried = float("-inf")
        self._reading = False

    def view(self, viewer_url: str, game_id: str) -> dict[str, Any] | None:
        """:func:`seconds_left` for ``game_id`` : one viewer read per :data:`REFRESH`.

        The read happens outside the lock, by one request at a time : the
        others answer at once with the last clock, so a slow viewer never
        holds the page's ``/state`` (review Admin #139).
        """
        with self._lock:
            now = self._monotonic()
            due = not self._reading and (game_id != self._game_id or now - self._tried >= REFRESH)
            if due:
                self._tried = now
                self._reading = True
        if due:
            try:
                self._read(viewer_url, game_id, now)
            finally:
                with self._lock:
                    self._reading = False
        with self._lock:
            if game_id != self._game_id:
                return None  # another game, not read yet
            return seconds_left(self._clock, self._monotonic() - self._fetched)

    def _read(self, viewer_url: str, game_id: str, now: float) -> None:
        url = f"{viewer_url}/api/clock?game_id={quote(game_id)}"
        try:
            body = viewer.get_json(url, timeout=TIMEOUT)
            clock = body.get("clock") if isinstance(body, dict) else None
        except Exception as exc:  # display only : the clock never breaks the page's state
            log.warning("clock of %s unreadable (%s)", game_id, exc.__class__.__name__)
            with self._lock:
                if game_id != self._game_id:
                    # Another game : nothing of the previous clock may stay on screen.
                    self._game_id, self._clock, self._fetched = game_id, None, now
            # Same game : the last clock stays, and keeps running from its read.
            return
        with self._lock:
            self._game_id, self._clock, self._fetched = game_id, clock, now
