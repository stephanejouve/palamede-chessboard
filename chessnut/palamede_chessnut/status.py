"""What the app's page shows (#78) : a snapshot of the bridge, never its token."""

from __future__ import annotations

import collections
import logging
from typing import TYPE_CHECKING, Any

import chess

from palamede_chessnut.moves import differing_squares

if TYPE_CHECKING:
    from palamede_chessnut.bridge import Bridge

#: Lines kept for the page's « Journal » panel.
RECENT_LINES = 200


class RecentLog(logging.Handler):
    """The last log lines, for the page ; the bridge never logs its token."""

    def __init__(self, capacity: int = RECENT_LINES) -> None:
        super().__init__()
        self.lines: collections.deque[str] = collections.deque(maxlen=capacity)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def sync_light(bridge: Bridge) -> str:
    """``"waiting"`` (no game or board yet), ``"on"`` (the board matches), ``"fixing"``."""
    if bridge.game is None or bridge.observed is None:
        return "waiting"
    if bridge.observed.board_fen() == bridge.game.board_fen():
        return "on"
    return "fixing"


def snapshot(bridge: Bridge, recent: RecentLog | None = None) -> dict[str, Any]:
    """The JSON document behind the page's ``/state``."""
    game, observed = bridge.game, bridge.observed
    differing = (
        sorted(
            chess.square_name(s)
            for s in differing_squares(chess.BaseBoard(game.board_fen()), observed)
        )
        if game is not None and observed is not None
        else []
    )
    return {
        "game_id": bridge.game_id,
        "color": "white" if bridge.player == chess.WHITE else "black",
        "fen": game.fen() if game is not None else None,
        "board_fen": observed.board_fen() if observed is not None else None,
        "history": list(bridge.history),
        "live": bridge.live,
        "finished": bridge.finished,
        "to_relay": bridge.to_relay,
        "differing": differing,
        "lights": {
            "bluetooth": "on" if bridge.board_link == "connected" else "searching",
            "server": "on" if bridge.stream_link == "connected" else bridge.stream_link,
            "sync": sync_light(bridge),
        },
        "log": list(recent.lines) if recent is not None else [],
    }
