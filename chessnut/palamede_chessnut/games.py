"""« Fichier ▸ Ouvrir » (#90, first part) : another game of the player, same window.

The page lists the games in progress of the token's account, through the
viewer's ``GET /api/games?player=<account>`` (#46) : the account comes from
the token's light (``/seat``, #102), never from the page. Opening one asks
``/seat`` for the new game first : the colour is the one the account plays
there, and a game the account does not play is refused before the running
bridge is touched. Then that bridge stops (LEDs off) and a new one, same
token, same board, takes its place.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import chess

from palamede_chessnut import viewer
from palamede_chessnut.bridge import Bridge

log = logging.getLogger(__name__)

#: The viewer's own rule for a game_id (``palamede_chessboard.paths``).
GAME_ID = re.compile(r"[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,63}")
#: Time left to the running bridge to switch the LEDs off and close the board's link.
STOP_SECONDS = 5.0
NO_ACCOUNT = "Le jeton n'est pas encore reconnu : la liste attend le voyant Jeton vert."
NO_SERVER = "Le serveur ne répond pas : réessayez dans un instant."
BOARD_BUSY = (
    "L'échiquier n'est pas encore libéré par la partie précédente : réessayez dans un instant."
)
NOT_PLAYED = "Votre compte ne joue pas cette partie."
#: ``/seat`` status → what the page gets ; anything else is :data:`NO_SERVER`.
SEAT_REFUSALS = {
    401: (403, "Jeton refusé par le serveur : la partie n'est pas ouverte."),
    403: (403, NOT_PLAYED),
    404: (404, "Cette partie n'existe pas sur le serveur."),
}


def player_games(viewer_url: str, account: str) -> list[dict[str, str]] | None:
    """The account's games in progress, newest first ; ``None`` if the viewer is unreadable."""
    try:
        data = viewer.get_json(f"{viewer_url}/api/games?player={quote(account)}")
        entries = data["in_progress"]
        return [
            {key: str(entry.get(key) or "") for key in ("game_id", "white", "black")}
            for entry in entries
        ]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("games of %s unreadable (%s)", account, exc)
        return None


def chosen_color(colors: list[str], current: chess.Color) -> chess.Color | None:
    """The side the account plays ; the current one when it plays both (a game against oneself)."""
    sides = [
        chess.WHITE if c == "white" else chess.BLACK for c in colors if c in ("white", "black")
    ]
    if current in sides:
        return current
    return sides[0] if sides else None


class Session:
    """The bridge the window shows, and its thread : replaced when another game is opened."""

    def __init__(
        self,
        bridge: Bridge,
        run: Callable[[Bridge], None],
        on_change: Callable[[Bridge], None] | None = None,
    ) -> None:
        self.bridge = bridge
        self.run = run
        #: Told of each new bridge : the page server, the window's title.
        self.on_change = on_change
        self.worker: threading.Thread | None = None
        self.lock = threading.Lock()

    def start(self) -> None:
        self.worker = threading.Thread(target=self.run, args=(self.bridge,), daemon=True)
        self.worker.start()

    def stop(self) -> bool:
        """Stop the running bridge : ``False`` while its thread still runs after :data:`STOP_SECONDS`."""
        self.bridge.stopped.set()
        if self.worker is not None:
            self.worker.join(timeout=STOP_SECONDS)
            if self.worker.is_alive():
                log.warning("the bridge of %s is still stopping", self.bridge.game_id)
                return False
        return True

    def games(self) -> tuple[int, dict[str, Any]]:
        """``(HTTP status, body)`` for the page's list."""
        account = self.bridge.account
        if not account:
            return 409, {"error": NO_ACCOUNT}
        entries = player_games(self.bridge.viewer, account)
        if entries is None:
            return 502, {"error": NO_SERVER}
        return 200, {"account": account, "current": self.bridge.game_id, "games": entries}

    def open_game(self, game_id: str) -> tuple[int, str]:
        """``(HTTP status, message)`` : 200 once the new bridge runs."""
        if not GAME_ID.fullmatch(game_id):
            return 400, "nom de partie illisible"
        with self.lock:
            old = self.bridge
            if game_id == old.game_id:
                return 200, ""
            new = Bridge(
                old.viewer,
                game_id,
                old.player,
                old._token,  # the same token : it never leaves the bridges
                with_board=old.with_board,
                board_transport=old.board_transport,
            )
            status, seat = new.seat()
            if status != 200 or not isinstance(seat, dict):
                return SEAT_REFUSALS.get(status or 0, (502, NO_SERVER))
            color = chosen_color(list(seat.get("colors") or []), old.player)
            if color is None:
                return 403, NOT_PLAYED
            new.player = color
            log.info("opening %s as %s", game_id, "white" if color else "black")
            if not self.stop():
                # Two bridges would talk to the same board (review Admin #121) :
                # the old one, stopped, stays shown until a new try finds it gone.
                return 503, BOARD_BUSY
            self.bridge = new
            if self.on_change is not None:
                self.on_change(new)
            self.start()
        return 200, ""
