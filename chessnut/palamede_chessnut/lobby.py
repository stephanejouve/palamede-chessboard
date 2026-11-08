"""No game on the board (#167) : two rooks lifted for 5 s open one.

The app starts without a game in progress, or one is over : a lobby then
holds the board instead of a game's bridge. It reads the placement only.
The two corners of one side emptied together and left empty for
:data:`GESTURE_SECONDS` open a game for that side, against the default
opponent of :func:`.config.new_game` (``bot-maia-1100`` in 15+10), through
the viewer's ``POST /api/games`` (#126) with the player's token. The rest
of the board may be anything : once the game is opened, its bridge guides
the setup square by square (#117), so that a beginner is never stuck on a
queen one square off (smoke of Stéphane 02/11, #175).

* a1 + h1 emptied : White ; a8 + h8 emptied : Black. The corners, not the
  rooks : the piece is not looked at (a Chess960 start keeps a piece there) ;
* each corner must have been seen occupied first, so pieces set up with the
  corners last open nothing (review Junior and Admin #170) ;
* the two corners are emptied within :data:`TOGETHER_SECONDS` of each other,
  and the other side's corners stay occupied : putting the pieces away one
  by one after a game opens nothing (owner, 05/11) ;
* while the gesture holds, the two squares blink ; once the game is opened,
  a light runs along the player's back rank, then the window moves to the
  new game the way « Ouvrir une partie » does (#90, #121) ;
* a refusal (429 too many games, 422 opponent, 401 token) is said in the
  window, without a light, and not tried again until the rooks move.

From the starting position too, the white knights choose the clock of the
games opened next (#186, owner 04/11) : either knight put on rank 3 the base
time, on rank 4 the increment, the file saying the value (a3 3 min … f3
90 min, a4 0 s … f4 30 s). Both may be off at once, the one put last
winning its rank (smoke of Stéphane, review Admin, 04/11). A value is taken
once its knight has stood :data:`CHOICE_SECONDS` on its square, so that a
square crossed on the way chooses nothing, and not again while it stays ;
the square blinks, the window says the clock (« déjà en place » when it
does not change). The knights go home before the rooks' gesture. The clock
lives as long as the app : it does not rewrite the settings file, which
also says where the token is sent.

The white bishops lifted together open the exercise the coach suggests,
the white knights the lesson of the day, not there yet : see
:mod:`.coach_gestures` (#186, step 2). Each gesture taken is said by the
board's sound code (:mod:`.sounds`) : two high beeps, two low ones for a
refusal.

A lobby that follows a finished game keeps that game's name and moves on
screen ; the board drawn is the one read, where the pieces of the next game
are set up, not the finished game's last position (#261).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import chess

from palamede_chessnut import coach_gestures, config, leds, viewer
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
#: Two hands lift two corners together ; one by one is putting the pieces away (owner, 05/11).
TOGETHER_SECONDS = 1.0
CORNERS = (*GESTURES[chess.WHITE], *GESTURES[chess.BLACK])
#: Corner → when it was emptied, ``None`` while occupied ; absent until seen occupied.
Emptied = dict[chess.Square, float | None]
#: Ticks come every 0.1 s while the board is linked : a longer gap is a lost link.
LINK_GAP_SECONDS = 1.0
#: The knight still on its square that long : a choice, not a square crossed (owner, 2 to 3 s).
CHOICE_SECONDS = 2.0
#: Part of the clock (0 the base minutes, 1 the increment seconds) → the rank
#: a white knight chooses it on, and the values from the a-file on (owner
#: 02/11, 04/11) ; either knight, both may be off at once (smoke of Stéphane, 04/11).
CHOICES = {
    0: (2, (3, 5, 10, 15, 25, 90)),
    1: (3, (0, 2, 3, 5, 10, 30)),
}
KNIGHT_HOMES = (chess.B1, chess.G1)
#: A knight's choice : ``(part, value, square)``.
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


def gesture_side(observed: chess.BaseBoard, emptied: Emptied) -> chess.Color | None:
    """The side whose two corners were emptied together, the other's still occupied, else ``None``."""
    for side, squares in GESTURES.items():
        times = [emptied.get(square) for square in squares]
        if any(observed.piece_at(square) is not None for square in squares):
            continue
        if times[0] is None or times[1] is None or abs(times[0] - times[1]) > TOGETHER_SECONDS:
            continue
        if all(observed.piece_at(square) is not None for square in GESTURES[not side]):
            return side
    return None


def knights_off(observed: chess.BaseBoard) -> list[chess.Square] | None:
    """The starting position but for its white knights : the squares of those off b1 and g1.

    Empty while they are home or lifted ; ``None`` for any other change,
    a spare knight included (one more knight than homes left).
    """
    knight = chess.Piece(chess.KNIGHT, chess.WHITE)
    rest = _without(KNIGHT_HOMES)
    off = []
    for square in chess.SQUARES:
        piece = observed.piece_at(square)
        if piece == rest.piece_at(square) or (square in KNIGHT_HOMES and piece == knight):
            continue
        if piece != knight or rest.piece_at(square) is not None:
            return None
        off.append(square)
    left = sum(observed.piece_at(home) != knight for home in KNIGHT_HOMES)
    return off if len(off) <= left else None


def choices(observed: chess.BaseBoard) -> tuple[Choice, ...]:
    """The clock's parts and values the knights' squares say (g3, h3, the other ranks say nothing)."""
    found = []
    for square in knights_off(observed) or ():
        for part, (rank, values) in CHOICES.items():
            file = chess.square_file(square)
            if chess.square_rank(square) == rank and file < len(values):
                found.append((part, values[file], square))
    return tuple(found)


def on_the_way(observed: chess.BaseBoard) -> bool:
    """The starting position, one or both rooks of a side lifted, knights off : the gestures' states."""
    pairs = (*GESTURES.values(), coach_gestures.PAIRS[coach_gestures.EXERCISE])
    return knights_off(observed) is not None or any(
        observed == _without(lifted)
        for squares in pairs
        for lifted in ((), squares[:1], squares[1:], squares)
    )


def chosen(time_control: str, picked: tuple[Choice, ...]) -> str:
    """``time_control`` with each part picked set to its value ; without a clock, from the default one."""
    if time_control == "unrated":
        time_control = config.NEW_GAME["time_control"]
    clock = time_control.split("+")
    for part, value, _square in picked:
        clock[part] = str(value)
    return "+".join(clock)


def tips() -> dict[str, str]:
    """Square → the value the knight chooses there, for the screen's board (smoke of Stéphane, 04/11)."""
    unit = {0: "′", 1: ""}
    sign = {0: "", 1: "+"}
    return {
        chess.square_name(chess.square(file, rank)): f"{sign[part]}{value}{unit[part]}"
        for part, (rank, values) in CHOICES.items()
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
            # The finished game's name and moves stay on screen ; its board is
            # no longer compared, nor drawn (#261).
            self.game, self.history, self.start_fen = after.game, after.history, after.start_fen
            self.finished = True
            self.account, self.token_light = after.account, after.token_light
            self.stream_link = after.stream_link
        #: Set by the session : moves the window to the new game.
        self.on_created: OnCreated | None = None
        #: ``settled_since`` of the placement already tried : not again until it changes.
        self.tried: float | None = None
        #: The corners' times of the gesture already tried : another piece moved
        #: does not try again, the corners must be seen occupied first.
        self.tried_corners: tuple[float | None, ...] = ()
        self.creating = False
        #: The new game, once opened : handed over after the chaser.
        self.created: str | None = None
        self.counting: chess.Color | None = None
        #: The coach's gesture held (:data:`.coach_gestures.PAIRS`).
        self.pairing: str | None = None
        #: The knights' new choices waiting for :data:`CHOICE_SECONDS`.
        self.choosing: tuple[Choice, ...] = ()
        #: The squares whose choice is taken : not again while the knight stays.
        self.taken: set[chess.Square] = set()
        #: The ranks (1-8) two knights were just put on together : neither counts.
        self.clash: list[int] = []
        #: ``settled_since`` of the placement whose idle knight is logged.
        self.noted: float | None = None
        self.refused_text: str | None = None
        #: The whole starting position stood on the board since the last other
        #: placement : the knights may choose the clock.
        self.armed = False
        #: When each corner was emptied : the rooks' gesture (#175).
        self.emptied: Emptied = {}
        self.last_tick: float | None = None

    def lobby_text(self) -> str | None:
        if self.created is not None:
            return f"Partie {self.created} ouverte : la fenêtre la rejoint."
        if self.creating:
            return "Création de la partie…"
        if self.refused_text is not None:
            return self.refused_text
        if self.counting is not None or self.pairing is not None:
            left = max(GESTURE_SECONDS - (time.monotonic() - self.settled_since), 0.0)
            if self.pairing == coach_gestures.EXERCISE:
                return f"Exercice dans {left:.0f} s…"
            if self.pairing == coach_gestures.LESSON:
                return f"Leçon du jour dans {left:.0f} s…"
            assert self.counting is not None
            return f"Nouvelle partie avec les {SIDES[self.counting]} dans {left:.0f} s…"
        clock = self.new_game["time_control"]
        if self.clash:
            ranks = " et ".join(str(rank) for rank in self.clash)
            return f"Deux cavaliers sur la rangée {ranks} : retirez-en un."
        if self.choosing:
            left = max(CHOICE_SECONDS - (time.monotonic() - self.settled_since), 0.0)
            new = chosen(clock, self.choosing)
            # The value already in place is said : else nothing seems to happen (Stéphane, 04/11).
            already = " (déjà en place)" if new == clock else ""
            return f"Cadence {new}{already} dans {left:.0f} s…"
        state = "Partie terminée" if self.finished else "Aucune partie"
        return f"{state}, cadence {said(clock)}."

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        if task is not None:
            cancel_on_sigterm(loop, task)
        await self.run_board()

    async def tick(self, write: Any) -> None:
        now = time.monotonic()
        if self.last_tick is not None and now - self.last_tick > LINK_GAP_SECONDS:
            # The link was lost : corners emptied meanwhile would get one time (review Junior #214).
            self.emptied.clear()
        self.last_tick = now
        if self.observed is not None:
            # At every tick, an animation's too : each corner keeps its own time.
            self._watch_corners(self.observed)
        if self.animation:
            await self._show(write, self.animation.pop(0))
            if not self.animation and self.created is not None:
                threading.Thread(target=self._hand_over, daemon=True).start()
            return
        if self.observed is None or self.creating or self.created is not None:
            return
        # What the board was read as, in the log : the smoke can then tell a
        # gesture not seen from one not made (smoke of Stéphane, 04/11).
        if self.observed == chess.BaseBoard():
            if not self.armed:
                log.info("starting position on the board : clock gestures armed")
            self.armed = True
        elif not on_the_way(self.observed):
            if self.armed:
                log.info(
                    "clock gestures disarmed until the starting position : %s",
                    self.observed.board_fen(),
                )
            self.armed = False
        side = gesture_side(self.observed, self.emptied)
        pair = coach_gestures.pair_gesture(self.observed) if self.armed else None
        standing = choices(self.observed) if self.armed else ()
        # A knight gone forgets its square ; one still there is not taken again.
        self.taken &= {square for _part, _value, square in standing}
        # The knight put last wins its rank (review Admin #191) : only the new
        # ones count, and two put at once on one rank say nothing.
        fresh = [c for c in standing if c[2] not in self.taken]
        parts = [part for part, _value, _square in fresh]
        picked = tuple(c for c in fresh if parts.count(c[0]) == 1)
        # Said, else nothing seems to happen (review Junior #191).
        self.clash = sorted({CHOICES[part][0] + 1 for part in parts if parts.count(part) > 1})
        self._note_idle_knights(self.observed, standing)
        corners = tuple(self.emptied.get(s) for s in GESTURES[side]) if side is not None else ()
        tried = self.tried == self.settled_since or (
            bool(corners) and corners == self.tried_corners
        )
        if (side is None and not picked and pair is None) or tried:
            if side is None and not picked and pair is None:
                self.refused_text = None  # the pieces are back : a new try may come
            self.counting, self.choosing, self.pairing = None, (), None
            await self._show(write, frozenset())
            return
        self.counting, self.choosing = side, picked
        self.pairing = pair if side is None and not picked else None
        self.refused_text = None
        held = time.monotonic() - self.settled_since
        wait, squares = (
            (CHOICE_SECONDS, frozenset(square for _part, _value, square in picked))
            if picked
            else (GESTURE_SECONDS, frozenset(GESTURES[side]))
            if side is not None
            else (GESTURE_SECONDS, frozenset(coach_gestures.PAIRS[pair]) if pair else frozenset())
        )
        if held < wait:
            await self._show(write, leds.blink(squares, held))
            return
        self.tried, self.counting, self.choosing = self.settled_since, None, ()
        self.pairing = None
        await self._show(write, frozenset())
        if picked:
            self._choose(picked)
        elif side is not None:
            self.tried_corners = corners
            await self._create(side)
        elif pair is not None:
            await self._coach(pair)

    def _watch_corners(self, observed: chess.BaseBoard) -> None:
        """Notes when each corner, once seen occupied, was emptied : when its placement appeared."""
        for square in CORNERS:
            if observed.piece_at(square) is not None:
                self.emptied[square] = None
            elif square in self.emptied and self.emptied[square] is None:
                self.emptied[square] = self.settled_since

    def _note_idle_knights(self, observed: chess.BaseBoard, standing: tuple[Choice, ...]) -> None:
        """Logs, once per placement, a knight standing where no value is."""
        held = time.monotonic() - self.settled_since
        if not self.armed or self.noted == self.settled_since or held < CHOICE_SECONDS:
            return
        valued = {square for _part, _value, square in standing}
        idle = [s for s in knights_off(observed) or () if s not in valued]
        if idle:
            self.noted = self.settled_since
            names = " ".join(chess.square_name(s) for s in idle)
            log.info("knight on %s : no value there (a3-f3, a4-f4)", names)

    def _choose(self, picked: tuple[Choice, ...]) -> None:
        """The clock with the knights' values, their squares blinking to say so."""
        clock = chosen(self.new_game["time_control"], picked)
        squares = frozenset(square for _part, _value, square in picked)
        log.info(
            "clock chosen at %s : %s → %s",
            " ".join(chess.square_name(s) for s in sorted(squares)),
            self.new_game["time_control"],
            clock,
        )
        self.new_game["time_control"] = clock
        self.taken |= squares
        self.animation = leds.chosen(squares)
        self.say("ok")

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
            self.say("ok")
            return
        if status == 401:
            self.token_light = "refused"
        self.refused_text = refusal(status, body)
        self.say("no")
        log.warning("new game refused : HTTP %s %s", status, self.refused_text)

    async def _coach(self, pair: str) -> None:
        """The coach's gesture taken : the suggested exercise opened, or the lesson said to come."""
        if pair == coach_gestures.LESSON:
            log.info("two knights lifted : lessons are not there yet")
            self.refused_text = coach_gestures.SOON
            self.say("no")
            return
        log.info("two bishops lifted : the exercise the coach suggests")
        self.creating = True
        try:
            game_id, side, refused = await asyncio.to_thread(
                coach_gestures.start_exercise, self.viewer, self._token
            )
        finally:
            self.creating = False
        if game_id is None:
            self.refused_text = refused
            self.say("no")
            log.warning("exercise not opened : %s", refused)
            return
        log.info("exercise %s opened", game_id)
        self.player, self.created = side, game_id
        self.animation = leds.chaser(0 if side == chess.WHITE else 7)
        self.say("ok")

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
