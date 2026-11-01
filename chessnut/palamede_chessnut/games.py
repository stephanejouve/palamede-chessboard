"""« Fichier ▸ Ouvrir » (#90, first part) : another game of the player, same window.

The page lists the games in progress of the token's account, through the
viewer's ``GET /api/games?player=<account>`` (#46) : the account comes from
the token's light (``/seat``, #102), never from the page. Opening one asks
``/seat`` for the new game first : the colour is the one the account plays
there, and a game the account does not play is refused before the running
bridge is touched. Then that bridge stops (LEDs off) and a new one, same
token, same board, takes its place.

The « Échiquier » switch (#152) replaces the bridge the same way, on the
same game, with the board or without it (mouse play).

Without a game on the board, a :class:`~.lobby.Lobby` holds it (#167) : at
launch when the account has none in progress, and once a board's game is
over. The game its gesture opens is opened here, as a chosen one.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import chess

from palamede_chessnut import clock_hold, viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.lobby import Lobby

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
CLOSING = "La fenêtre se ferme."
NO_GAME = "Aucune partie à jouer à la souris : levez deux tours sur l'échiquier."
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
        #: The window is closed : no game may be opened any more.
        self.closed = False

    def start(self) -> None:
        if isinstance(self.bridge, Lobby):
            self.bridge.on_created = self.open_game
        self.worker = threading.Thread(target=self._run, args=(self.bridge,), daemon=True)
        self.worker.start()

    def _run(self, bridge: Bridge) -> None:
        """The bridge's thread ; a board's game over, a lobby takes the board (#167)."""
        self.run(bridge)
        if bridge.finished and bridge.with_board and not isinstance(bridge, Lobby):
            # Another thread : replacing joins this one.
            threading.Thread(target=self._idle, args=(bridge,), daemon=True).start()

    def _idle(self, done: Bridge) -> None:
        with self.lock:
            if self.closed or self.bridge is not done:
                return  # closed, or another game opened meanwhile
            lobby = Lobby(
                done.viewer,
                done._token,  # the same token : it never leaves the bridges
                board_transport=done.board_transport,
                new_game=done.new_game,
                after=done,
            )
            log.info("game %s over : two rooks lifted open a new one", done.game_id)
            if not self._replace(lobby):
                return
        self._tell(lobby)

    def stop(self) -> bool:
        """Stop the running bridge : ``False`` while its thread still runs after :data:`STOP_SECONDS`."""
        self.bridge.stopped.set()
        if self.worker is not None:
            self.worker.join(timeout=STOP_SECONDS)
            if self.worker.is_alive():
                log.warning("the bridge of %s is still stopping", self.bridge.game_id)
                return False
        return True

    def close(self) -> None:
        """The window is closing : stop the bridge, after any opening under way (review Junior #121).

        A board's bridge then says it no longer plays on the board : the time of
        a player who goes on elsewhere is no longer held (#164).
        """
        with self.lock:
            self.closed = True
            self.stop()
            bridge = self.bridge
        # Outside the lock : the viewer may take up to 10 s to answer (review Admin #166).
        if bridge.with_board and not isinstance(bridge, Lobby):  # a lobby plays no game
            clock_hold.declare(bridge, False)

    def games(self) -> tuple[int, dict[str, Any]]:
        """``(HTTP status, body)`` for the page's list."""
        account = self.bridge.account
        if not account:
            return 409, {"error": NO_ACCOUNT}
        entries = player_games(self.bridge.viewer, account)
        if entries is None:
            return 502, {"error": NO_SERVER}
        # A stopped game is not « open » : the page lets the player choose it again (Junior #123).
        idle = self.bridge.stopped.is_set() or isinstance(self.bridge, Lobby)
        current = None if idle else self.bridge.game_id
        return 200, {"account": account, "current": current, "games": entries}

    def open_game(self, game_id: str) -> tuple[int, str]:
        """``(HTTP status, message)`` : 200 once the new bridge runs."""
        if not GAME_ID.fullmatch(game_id):
            return 400, "nom de partie illisible"
        with self.lock:
            old = self.bridge
            if self.closed:
                return 503, CLOSING
            # A stopped bridge (503 above, game over) may be opened again (review Junior #121).
            # A lobby shows a finished game, which may be opened again.
            if game_id == old.game_id and not old.stopped.is_set() and not isinstance(old, Lobby):
                return 200, ""
            new = successor(old, game_id, old.with_board)
            status, seat = new.seat()
            if status != 200 or not isinstance(seat, dict):
                return SEAT_REFUSALS.get(status or 0, (502, NO_SERVER))
            color = chosen_color(list(seat.get("colors") or []), old.player)
            if color is None:
                return 403, NOT_PLAYED
            new.player = color
            log.info("opening %s as %s", game_id, "white" if color else "black")
            if not self._replace(new):
                return 503, BOARD_BUSY
        self._tell(new)
        return 200, ""

    def switch_board(self, with_board: bool) -> tuple[int, str]:
        """« Échiquier » on or off (#152) : the same game, played on the board or with the mouse.

        The new bridge reads the game from the stream again : a move of the
        opponent that came during the switch is in its first snapshot. Back on
        the board, the setup tray says what to move, as at launch.
        """
        with self.lock:
            old = self.bridge
            if self.closed:
                return 503, CLOSING
            if isinstance(old, Lobby):
                return 409, NO_GAME
            if with_board == old.with_board and not old.stopped.is_set():
                return 200, ""
            new = successor(old, old.game_id, with_board)
            log.info("%s : %s", old.game_id, "board" if with_board else "mouse")
            if not self._replace(new):
                return 503, BOARD_BUSY
        self._tell(new)
        return 200, ""

    def _replace(self, new: Bridge) -> bool:
        """Under the lock : stop the running bridge, then start ``new`` ; ``False`` if it still runs."""
        if not self.stop():
            # Two bridges would talk to the same board (review Admin #121) :
            # the old one, stopped, stays shown until a new try finds it gone.
            return False
        self.bridge = new
        self.start()
        return True

    def _tell(self, new: Bridge) -> None:
        # Outside the lock, which close() waits for on the main thread : a backend that
        # sets the title synchronously on that thread would wait for us (review Admin #123).
        # Read after the lock : a close() that came in meanwhile has set it.
        if self.on_change is not None and not self.closed:
            self.on_change(new)


def successor(old: Bridge, game_id: str, with_board: bool) -> Bridge:
    """A new bridge for ``game_id``, same viewer, same side, same token and transport."""
    new = Bridge(
        old.viewer,
        game_id,
        old.player,
        old._token,  # the same token : it never leaves the bridges
        with_board=with_board,
        board_transport=old.board_transport,
    )
    new.new_game = old.new_game
    return new
