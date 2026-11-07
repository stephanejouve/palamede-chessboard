"""Where the bridge's game lives (#84) : the server it reads and plays on.

The bridge talks to its game server at five points only : the game's
stream, a move, a resignation, the player's seat and the server's
position. :class:`GameServer` names them ; :class:`ViewerServer` is the
palamède viewer, the only server until Lichess (#84, step 2). Everything
else — the LEDs, the move found on the board, the setup, the gestures —
does not depend on where the game lives.
"""

from __future__ import annotations

import urllib.request
from typing import Any, Protocol
from urllib.parse import quote

from palamede_chessnut import viewer

#: Above the viewer's 15 s SSE keepalive : a silent stream is a dead one.
SSE_READ_TIMEOUT = 45.0


class GameServer(Protocol):
    """The five points where the bridge meets the server of its game."""

    def open_stream(self) -> Any:
        """The game's event stream : a context manager yielding raw lines."""
        ...

    def post_move(self, uci: str) -> tuple[int | None, str]:
        """``(HTTP status, text)`` of playing ``uci`` ; ``None`` without an answer."""
        ...

    def post_resign(self) -> tuple[int | None, str]:
        """``(HTTP status, text)`` of resigning the game."""
        ...

    def seat(self) -> tuple[int | None, Any]:
        """``(HTTP status, body)`` : the sides the token's player holds."""
        ...

    def position(self) -> dict[str, Any] | None:
        """The server's position as a stream snapshot, ``None`` if unreadable."""
        ...


class ViewerServer:
    """A game of the palamède viewer, played with the player's token."""

    def __init__(self, viewer_url: str, game_id: str, token: str) -> None:
        self.viewer = viewer_url
        self.game_id = game_id
        self._token = token

    def _url(self, tail: str) -> str:
        return f"{self.viewer}/api/games/{quote(self.game_id)}/{tail}"

    def open_stream(self) -> Any:
        return urllib.request.urlopen(self._url("stream"), timeout=SSE_READ_TIMEOUT)

    def post_move(self, uci: str) -> tuple[int | None, str]:
        return viewer.post(self._url("move"), self._token, {"uci": uci})

    def post_resign(self) -> tuple[int | None, str]:
        return viewer.post(self._url("resign"), self._token)

    def seat(self) -> tuple[int | None, Any]:
        return viewer.get_with_token(self._url("seat"), self._token)

    def position(self) -> dict[str, Any] | None:
        return viewer.position(self.viewer, self.game_id)
