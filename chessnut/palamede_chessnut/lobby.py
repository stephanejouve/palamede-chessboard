"""No game on the board (#167) : two rooks lifted for 5 s open one.

The app starts without a game in progress, or one is over : a lobby then
holds the board instead of a game's bridge. It reads the placement only.
From the starting position, the two rooks of one side lifted together for
:data:`GESTURE_SECONDS` open a game for that side, against the default
opponent of :func:`.config.new_game` (``bot-maia-1100`` in 15+10), through
the viewer's ``POST /api/games`` (#126) with the player's token.

* a1 + h1 lifted : White ; a8 + h8 lifted : Black. No legal move takes two
  rooks off at once, and resigning lifts the king alone ;
* the whole starting position must have stood on the board first : pieces
  set up with the rooks last pass through « start without a1 + h1 », which
  opens nothing (review Junior and Admin #170) ;
* while the gesture holds, the two squares blink ; once the game is opened,
  a light runs along the player's back rank, then the window moves to the
  new game the way « Ouvrir une partie » does (#90, #121) ;
* a refusal (429 too many games, 422 opponent, 401 token) is said in the
  window, without a light, and not tried again until the rooks move.

From the starting position too, a white knight chooses the clock of the
games opened next (#186, owner 04/11) : the g1 knight put on rank 3 the base
time, the b1 knight on rank 4 the increment, the file saying the value
(a3 3 min … f3 90 min, a4 0 s … f4 30 s). It is taken once the knight has
stood :data:`CHOICE_SECONDS` on its square, so that a square crossed on the
way chooses nothing ; the square blinks, the window says the clock, and the
knight goes back home. The clock lives as long as the app : it does not
rewrite the settings file, which also says where the token is sent.

A lobby that follows a finished game keeps that game on screen.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import chess

from palamede_chessnut import config, leds, viewer
from palamede_chessnut.bridge import RESIGN_SECONDS, Bridge, cancel_on_sigterm

log = logging.getLogger(__name__)

#: As long as resigning by the king (#75) : a gesture, not a hand passing by.
GESTURE_SECONDS = RESIGN_SECONDS
#: The rooks lifted → the side they open.
GESTURES = {
    chess.WHITE: (chess.A1, chess.H1),
    chess.BLACK: (chess.A8, chess.H8),
}
SIDES = {chess.WHITE: "Blancs", chess.BLACK: "Noirs"}
#: The knight still on its square that long : a choice, not a square crossed (owner, 2 to 3 s).
CHOICE_SECONDS = 2.0
#: Part of the clock (0 the base minutes, 1 the increment seconds) → the knight
#: that chooses it, its rank, and the values from the a-file on (owner 02/11, 04/11).
CHOICES = {
    0: (chess.G1, 2, (3, 5, 10, 15, 25, 90)),
    1: (chess.B1, 3, (0, 2, 3, 5, 10, 30)),
}
#: The choice under way : ``(part, value, square)``.
Choice = tuple[int, int, chess.Square]
#: HTTP status → what the window says when the server's own words are missing.
REFUSALS = {
    401: "Jeton refusé par le serveur : partie non créée.",
    422: "Le serveur refuse l'adversaire ou la cadence : partie non créée.",
    429: "Trop de parties en cours : terminez-en une d'abord.",
}
#: No answer, or one without the game : the server may have opened it (review Admin #170).
NO_SERVER = (
    "Le serveur n'a pas répondu : la partie a peut-être été créée, "
    "voyez « Ouvrir… » avant de réessayer."
)
UNREADABLE = "Réponse du serveur illisible : la partie a peut-être été créée, voyez « Ouvrir… »."
#: Told the new game : ``(HTTP status, message)``, as :meth:`.games.Session.open_game`.
OnCreated = Callable[[str], tuple[int, str]]


def _without(squares: tuple[chess.Square, ...]) -> chess.BaseBoard:
    start = chess.BaseBoard()
    for square in squares:
        start.remove_piece_at(square)
    return start


def gesture_side(observed: chess.BaseBoard) -> chess.Color | None:
    """The side whose two rooks are lifted off the starting position, else ``None``."""
    for side, squares in GESTURES.items():
        if observed == _without(squares):
            return side
    return None


def knight_moved(observed: chess.BaseBoard) -> tuple[chess.Square, chess.Square | None] | None:
    """A choosing knight off the starting position : its home, and its square (``None`` while lifted)."""
    knight = chess.Piece(chess.KNIGHT, chess.WHITE)
    for home, _rank, _values in CHOICES.values():
        rest = _without((home,))
        if observed == rest:
            return home, None
        added = [sq for sq in chess.SQUARES if observed.piece_at(sq) != rest.piece_at(sq)]
        if len(added) == 1 and rest.piece_at(added[0]) is None:
            if observed.piece_at(added[0]) == knight:
                return home, added[0]
    return None


def choice(observed: chess.BaseBoard) -> Choice | None:
    """The clock's part and value the knight's square says, else ``None`` (g3, h3, the wrong rank)."""
    home, square = knight_moved(observed) or (None, None)
    if square is None:
        return None
    for part, (knight, rank, values) in CHOICES.items():
        file = chess.square_file(square)
        if home == knight and chess.square_rank(square) == rank and file < len(values):
            return part, values[file], square
    return None


def on_the_way(observed: chess.BaseBoard) -> bool:
    """The starting position, one or both rooks of a side lifted, a choosing knight off : the gestures' states."""
    return knight_moved(observed) is not None or any(
        observed == _without(lifted)
        for squares in GESTURES.values()
        for lifted in ((), squares[:1], squares[1:], squares)
    )


def chosen(time_control: str, part: int, value: int) -> str:
    """``time_control`` with ``part`` set to ``value`` ; without a clock, from the default one."""
    if time_control == "unrated":
        time_control = config.NEW_GAME["time_control"]
    clock = time_control.split("+")
    clock[part] = str(value)
    return "+".join(clock)


def tips() -> dict[str, str]:
    """Square → the value the knight chooses there, for the screen's board (smoke of Stéphane, 04/11)."""
    unit = {0: "′", 1: ""}
    sign = {0: "", 1: "+"}
    return {
        chess.square_name(chess.square(file, rank)): f"{sign[part]}{value}{unit[part]}"
        for part, (_knight, rank, values) in CHOICES.items()
        for file, value in enumerate(values)
    }


def said(time_control: str) -> str:
    """The clock as the window says it."""
    return "sans pendule" if time_control == "unrated" else time_control


def refusal(status: int | None, body: Any) -> str:
    """What the window says of a game the server did not open."""
    if status is None:
        return NO_SERVER
    if 200 <= status < 300:
        return UNREADABLE
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail:
        return f"Partie non créée : {detail}"
    return REFUSALS.get(status, f"Partie non créée (HTTP {status}).")


class Lobby(Bridge):
    """The board without a game : watches for the gesture, opens the game, hands over."""

    def __init__(
        self,
        viewer_url: str,
        token: str,
        *,
        board_transport: str = "auto",
        new_game: dict[str, str] | None = None,
        after: Bridge | None = None,
    ) -> None:
        super().__init__(
            viewer_url,
            after.game_id if after is not None else "",
            chess.WHITE,
            token,
            board_transport=board_transport,
        )
        if new_game is not None:
            self.new_game = dict(new_game)
        # No stream to follow : the light says the viewer answered the launch.
        self.stream_link = "connected"
        if after is not None:
            # The finished game stays on screen, its board is no longer compared.
            self.game, self.history, self.start_fen = after.game, after.history, after.start_fen
            self.finished = True
            self.account, self.token_light = after.account, after.token_light
            self.stream_link = after.stream_link
        #: Set by the session : moves the window to the new game.
        self.on_created: OnCreated | None = None
        #: ``settled_since`` of the placement already tried : not again until it changes.
        self.tried: float | None = None
        self.creating = False
        #: The new game, once opened : handed over after the chaser.
        self.created: str | None = None
        self.counting: chess.Color | None = None
        #: The knight's choice waiting for :data:`CHOICE_SECONDS`.
        self.choosing: Choice | None = None
        self.refused_text: str | None = None
        #: The whole starting position stood on the board since the last other placement.
        self.armed = False

    def lobby_text(self) -> str | None:
        if self.created is not None:
            return f"Partie {self.created} ouverte : la fenêtre la rejoint."
        if self.creating:
            return "Création de la partie…"
        if self.refused_text is not None:
            return self.refused_text
        if self.counting is not None:
            left = max(GESTURE_SECONDS - (time.monotonic() - self.settled_since), 0.0)
            return f"Nouvelle partie avec les {SIDES[self.counting]} dans {left:.0f} s…"
        clock = self.new_game["time_control"]
        if self.choosing is not None:
            part, value, _square = self.choosing
            left = max(CHOICE_SECONDS - (time.monotonic() - self.settled_since), 0.0)
            return f"Cadence {chosen(clock, part, value)} dans {left:.0f} s…"
        state = "Partie terminée" if self.finished else "Aucune partie"
        return f"{state}, cadence {said(clock)}."

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        if task is not None:
            cancel_on_sigterm(loop, task)
        await self.run_board()

    async def tick(self, write: Any) -> None:
        if self.animation:
            await self._show(write, self.animation.pop(0))
            if not self.animation and self.created is not None:
                threading.Thread(target=self._hand_over, daemon=True).start()
            return
        if self.observed is None or self.creating or self.created is not None:
            return
        if self.observed == chess.BaseBoard():
            self.armed = True
        elif not on_the_way(self.observed):
            self.armed = False
        side = gesture_side(self.observed) if self.armed else None
        picked = choice(self.observed) if self.armed else None
        if (side is None and picked is None) or self.tried == self.settled_since:
            if side is None and picked is None:
                self.refused_text = None  # the pieces are back : a new try may come
            self.counting = self.choosing = None
            await self._show(write, frozenset())
            return
        self.counting, self.choosing = side, picked
        self.refused_text = None
        held = time.monotonic() - self.settled_since
        wait, squares = (
            (CHOICE_SECONDS, frozenset({picked[2]}))
            if picked is not None
            else (GESTURE_SECONDS, frozenset(GESTURES[side]) if side is not None else frozenset())
        )
        if held < wait:
            await self._show(write, leds.blink(squares, held))
            return
        self.tried, self.counting, self.choosing = self.settled_since, None, None
        await self._show(write, frozenset())
        if picked is not None:
            self._choose(*picked)
        elif side is not None:
            await self._create(side)

    def _choose(self, part: int, value: int, square: chess.Square) -> None:
        """The clock with the knight's value, its square blinking to say so."""
        clock = chosen(self.new_game["time_control"], part, value)
        log.info("clock chosen : %s → %s", self.new_game["time_control"], clock)
        self.new_game["time_control"] = clock
        self.animation = leds.chosen(square)

    async def _create(self, side: chess.Color) -> None:
        self.creating = True
        payload = {
            "opponent": self.new_game["opponent"],
            "color": "white" if side == chess.WHITE else "black",
            "time_control": {"format": self.new_game["time_control"]},
        }
        log.info(
            "two rooks lifted : new game as %s against %s", payload["color"], payload["opponent"]
        )
        try:
            status, body = await asyncio.to_thread(
                viewer.post_json, f"{self.viewer}/api/games", self._token, payload
            )
        finally:
            self.creating = False
        game_id = body.get("game_id") if isinstance(body, dict) else None
        if status is not None and 200 <= status < 300 and isinstance(game_id, str):
            log.info("game %s opened", game_id)
            self.player, self.created = side, game_id
            self.animation = leds.chaser(0 if side == chess.WHITE else 7)
            return
        if status == 401:
            self.token_light = "refused"
        self.refused_text = refusal(status, body)
        log.warning("new game refused : HTTP %s %s", status, self.refused_text)

    def _hand_over(self) -> None:
        """Off the board's loop : the session stops this lobby to start the new game's bridge."""
        assert self.created is not None
        if self.on_created is None:
            log.info("game %s opened : open it in a window", self.created)
            return
        status, message = self.on_created(self.created)
        if status != 200:
            log.warning("game %s opened but not shown (%s)", self.created, message)
            self.refused_text = f"Partie {self.created} créée, mais pas ouverte : {message}"
            self.created = None
