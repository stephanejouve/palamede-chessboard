"""The game's clock for the page (#135) : read from the viewer, shown in the window.

The bridge reads the viewer's ``GET /api/clock``, at most every
:data:`REFRESH` seconds whatever the page's polling, and gives the page what
is left to each side. Reading it is also what lets the server end a Tablier
game on time when nobody moves (``settle_flag``, #135) : the window open is
enough. That read carries the player token : the server ends a game on time
only for a player of the game or its arbiter (owner, 31/10). The token
travels in the header, never in the URL nor in the page.
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
    """The last clock read for the game on screen.

    A daemon thread reads the viewer, one read at a time, every
    :data:`REFRESH` ; :meth:`view` never touches the network. A slow or
    unreachable viewer therefore never holds the page's ``/state`` (review
    Junior #139) : the board, the LEDs and the relay keep their pace.
    """

    def __init__(self, monotonic: Any = time.monotonic, *, thread: bool = True) -> None:
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = False
        self._use_thread = thread
        self._thread: threading.Thread | None = None
        #: ``(viewer, game, token)`` the page shows : what the thread reads.
        self._wanted: tuple[str, str, str | None] | None = None
        self._game_id: str | None = None
        self._clock: Any = None
        #: When the clock shown was read : the side to move keeps running from it.
        self._fetched = 0.0

    def view(
        self, viewer_url: str, game_id: str, token: str | None = None
    ) -> dict[str, Any] | None:
        """:func:`seconds_left` for ``game_id`` ; ``None`` until its first read.

        ``token`` : the player's ; without it, the public read of a spectator.
        """
        with self._lock:
            if self._wanted != (viewer_url, game_id, token):
                self._wanted = (viewer_url, game_id, token)
                self._wake.set()  # another game : read at once
            self._start()
            if game_id != self._game_id:
                return None
            return seconds_left(self._clock, self._monotonic() - self._fetched)

    def refresh(self) -> None:
        """One read of the game on screen (the thread's step ; tests call it directly)."""
        with self._lock:
            wanted = self._wanted
        if wanted is not None:
            self._read(*wanted)

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    def _start(self) -> None:
        if self._use_thread and self._thread is None and not self._closed:
            self._thread = threading.Thread(target=self._run, name="clock", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._closed:
            self._wake.clear()
            self.refresh()
            self._wake.wait(REFRESH)

    def _read(self, viewer_url: str, game_id: str, token: str | None = None) -> None:
        url = f"{viewer_url}/api/clock?game_id={quote(game_id)}"
        try:
            if token is None:
                body = viewer.get_json(url, timeout=TIMEOUT)
            else:
                status, body = viewer.get_with_token(url, token, timeout=TIMEOUT)
                if status != 200:
                    raise OSError(f"HTTP {status}")
            clock = body.get("clock") if isinstance(body, dict) else None
        except Exception as exc:  # display only : the clock never breaks the page's state
            log.warning("clock of %s unreadable (%s)", game_id, exc.__class__.__name__)
            with self._lock:
                if game_id != self._game_id:
                    # Another game : nothing of the previous clock may stay on screen.
                    self._game_id, self._clock, self._fetched = game_id, None, self._monotonic()
            # Same game : the last clock stays, and keeps running from its read.
            return
        with self._lock:
            if self._wanted is None or self._wanted[1] == game_id:
                self._game_id, self._clock, self._fetched = game_id, clock, self._monotonic()
