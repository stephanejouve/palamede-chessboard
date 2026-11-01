"""The relay hold (#164) : the board's player does not pay for setting the opponent's move.

After each opponent move, the server holds the time of a side played on an
eBoard, at most 20 s, until that side's bridge says the board matches the
game (owner, 01/11). The bridge declares its mode once it runs
(:func:`declare`) : a new bridge after the « Échiquier » switch (#152) says
it again, and closing the window says « no board » (:class:`.games.Session`).
On its own turn, it says when the board matches a new position
(:func:`synced`). An older viewer answers 404 : nothing is held, as before.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING
from urllib.parse import quote

from palamede_chessnut import viewer

if TYPE_CHECKING:
    from palamede_chessnut.bridge import Bridge

log = logging.getLogger(__name__)


def _url(bridge: Bridge, route: str) -> str:
    return f"{bridge.viewer}/api/games/{quote(bridge.game_id)}/{route}"


def declare(bridge: Bridge, with_board: bool | None = None) -> bool:
    """Tell the viewer whether this side is played on the board ; ``True`` once it knows."""
    board = bridge.with_board if with_board is None else with_board
    status, text = viewer.post(_url(bridge, "board"), bridge._token, {"board": board})
    if status != 200:
        log.info("relay hold unavailable on this viewer (HTTP %s %s)", status, text)
        return False
    return True


def synced(bridge: Bridge, board_fen: str) -> threading.Thread:
    """The board shows ``board_fen`` on the player's turn : their time may run.

    Off the board's loop, which must not wait for the network.
    """

    def post() -> None:
        status, text = viewer.post(_url(bridge, "synced"), bridge._token, {"fen": board_fen})
        if status != 200:
            log.debug("synced not taken (HTTP %s %s)", status, text)

    thread = threading.Thread(target=post, name="synced", daemon=True)
    thread.start()
    return thread
