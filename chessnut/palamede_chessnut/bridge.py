"""Bridge between a Chessnut Air+ and a game served by the viewer.

The SSE stream of the game keeps the expected position. Each time the
placement on the board has been still for :data:`.decide.SETTLE_SECONDS`,
:func:`.decide.decide` compares it with that position :

* same placement → LEDs off ;
* the player is to move, the board had caught up with the game, and a
  legal move leads to the placement → the move is posted, as the
  viewer's drag&drop would ;
* otherwise → the squares that differ light up : the opponent's move to
  replicate, a piece to put back, the setup to finish before the start.
  Once the board has caught up with the game (``synced``), this guidance
  waits :data:`.decide.GUIDE_SECONDS` on the player's own turn, so that holding a
  piece while thinking does not light its square.

Around that loop : a sweep of the LEDs when the board first matches the
game (from then on moves count), resignation by lifting one's own king
off the board for :data:`RESIGN_SECONDS`, and LEDs off once the game is
over and its final position stands on the board.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import threading
import time
from typing import Any
from urllib.parse import quote

import chess

from palamede_chessnut import clock_hold, config, exercise, leds, sounds, token_light, transport
from palamede_chessnut.decide import decide
from palamede_chessnut.designate import KING_HOLD_SECONDS, Designation, answers_on_board, follow
from palamede_chessnut.moves import only_king_lifted, relayed
from palamede_chessnut.protocol import UnknownPieceCode, decode_board, led_frame
from palamede_chessnut.server import GameServer, ViewerServer

log = logging.getLogger(__name__)

#: Long enough for a king held in hand while thinking (arbitration of Stéphane, #75).
RESIGN_SECONDS = 5.0
TICK_SECONDS = 0.1
RETRY_SECONDS = 3.0


class Bridge:
    """One physical board bound to one game of the viewer."""

    def __init__(
        self,
        viewer_url: str,
        game_id: str,
        player: chess.Color,
        token: str,
        *,
        with_board: bool = True,
        board_transport: str = "auto",
    ) -> None:
        self.viewer = viewer_url.rstrip("/")
        self.game_id = game_id
        self.player = player
        self._token = token
        #: Where the game lives (#84) : ``None`` for the palamède viewer, built
        #: from the attributes above at each call (see :attr:`server`).
        self.game_server: GameServer | None = None
        #: The token's light and account (#78, step A) : see :mod:`.token_light`.
        self.token_print = token_light.fingerprint(token)
        self.token_light = "checking"
        self.account: str | None = None
        #: ``False`` : no board, the app's window is played with the mouse (#92).
        self.with_board = with_board
        #: ``"ble"`` or ``"usb"`` : how the board is reached (#82), see :mod:`.transport`.
        self.board_transport = board_transport
        self.link_kind: str | None = None
        self.game: chess.Board | None = None
        self.live = False
        self.synced = False
        #: The board has matched the game once : the ready sweep was played.
        self.ready = False
        #: The game is over and its final position stood on the board.
        self.finished = False
        self.observed: chess.BaseBoard | None = None
        self.settled_since = 0.0
        self.pending: chess.Move | None = None
        self.refused: chess.Move | None = None
        #: What the viewer said of the refused move, shown under the board.
        self.refused_reason: str | None = None
        self.retry_at = 0.0
        #: Move received from the viewer (``g8f6 (Nf6)``), still to be played on the board.
        self.to_relay: str | None = None
        #: The position :attr:`to_relay` was played from : castling, en passant (#109).
        self.relay_from: chess.Board | None = None
        #: Posted move whose outcome is unknown (network error, 5xx).
        self.undelivered: chess.Move | None = None
        self.king_gone_since: float | None = None
        #: ``None``, ``"sent"`` or ``"unavailable"`` (refused, or old viewer).
        self.resign_state: str | None = None
        self.leds: frozenset[chess.Square] | None = None
        self.animation: list[leds.Frame] = []
        #: The sound code's series playing, one entry per tick (#186).
        self.sound: list[bytes | None] = []
        #: Set when the bridge stops : game over, or game impossible to follow.
        self.stopped = threading.Event()
        self.history: list[str] = []  #: moves so far (SAN), as the stream gives them
        self.start_fen: str | None = None  #: the position before them ; None : old server
        #: ``"searching"`` or ``"connected"`` : the Bluetooth link with the board.
        self.board_link = "searching"
        #: ``"connecting"``, ``"connected"`` or ``"reconnecting"`` : the viewer's stream.
        self.stream_link = "connecting"
        #: The game the two rooks lifted open (#167), see :mod:`.lobby`.
        self.new_game = dict(config.NEW_GAME)
        #: ``{"kind", "motif"}`` of an ``exo.*`` game, read from the coach (#237).
        self.exercise: dict[str, str] | None = None
        self.designation = Designation()

    def lobby_text(self) -> str | None:
        """What the page says instead of the game's status : only a lobby has something (#167)."""
        return None

    # ---- viewer side ------------------------------------------------------

    @property
    def server(self) -> GameServer:
        """The game's server (#84) : the viewer unless another one was given."""
        if self.game_server is not None:
            return self.game_server
        return ViewerServer(self.viewer, self.game_id, self._token)

    def apply_stream(self, payload: dict[str, Any]) -> None:
        """New snapshot from the SSE stream (shape : ``_game_stream_payload``)."""
        game = chess.Board(payload["fen"])
        self.live = not payload.get("is_game_over", False)
        if self.game is not None and game.fen() == self.game.fen():
            # Reconnection snapshot : a posted move stays pending until the
            # position actually changes, or it would be posted twice.
            return
        log.info("game %s : ply %s, %s to move", self.game_id, payload["ply"], payload["turn"])
        history = payload.get("san_history") or []
        self.history, self.start_fen = list(history), payload.get("start_fen")
        # A move the board does not show yet : the opponent's, or one played in
        # the viewer (mixed game). Not before the setup, which relays nothing.
        behind = self.observed is None or self.observed.board_fen() != game.board_fen()
        self.to_relay = (
            relayed(self.game, history[-1], game) if self.ready and history and behind else None
        )
        self.relay_from = self.game
        self.game = game
        self.synced = False
        self.pending = None
        self.undelivered = None

    def stream_opened(self, back: bool = False) -> None:
        """The stream is (re)connected : a viewer that refused resignation may have been redeployed.

        Seen on the board (chessnut-abandon-001) : a 404 from the old viewer
        kept the gesture off after the upgrade, until the bridge was restarted.
        Any refusal (404/405, but also 401/403 : a token fixed meanwhile)
        gets one new try per gesture once the stream is back.
        ``back`` : it had been lost ; said in the log, since a reconnection on
        an unchanged position is otherwise silent (review Admin #75).
        """
        self.stream_link = "connected"
        if back:
            log.info("stream of %s back", self.game_id)
        if self.resign_state == "unavailable":
            log.info("stream connected : resignation will be tried again")
            self.resign_state = None

    def _follow_stream(self, loop: asyncio.AbstractEventLoop) -> None:
        """Blocking SSE reader (own thread), reconnecting until :attr:`stopped`."""
        lost = False
        while not self.stopped.is_set():
            try:
                with self.server.open_stream() as response:
                    loop.call_soon_threadsafe(self.stream_opened, lost)
                    lost = False
                    for raw in response:
                        if self.stopped.is_set():  # window closed, or another game opened (#90)
                            return
                        line = raw.decode("utf-8").strip()
                        if not line.startswith("data:"):
                            continue
                        payload = json.loads(line[len("data:") :])
                        if "error" in payload:
                            log.error("stream of %s : %s", self.game_id, payload["error"])
                            self.stopped.set()
                            return
                        loop.call_soon_threadsafe(self.apply_stream, payload)
            except (OSError, ValueError) as exc:
                log.warning("stream of %s lost (%s), reconnecting", self.game_id, exc)
                lost = True
                self.stream_link = "reconnecting"
            time.sleep(RETRY_SECONDS)

    def post_move(self, uci: str) -> tuple[str | None, bool]:
        """Play ``uci`` through the viewer : ``(None, False)``, or ``(message, refused)``.

        ``refused`` only for a 4xx, when the viewer judged the move. A network
        error or a 5xx says nothing about the move, which is tried again. A
        3xx means a wrong ``--viewer`` : the bridge stops (review Admin #71).
        """
        status, text = self.server.post_move(uci)
        if status is not None and 200 <= status < 300:
            return None, False
        if status == 401:
            self.token_light = "refused"
        if status is not None and 300 <= status < 400:
            url = f"{self.viewer}/api/games/{quote(self.game_id)}/move"
            log.error("the viewer redirects %s (HTTP %s) : check --viewer", url, status)
            self.stopped.set()
        message = f"HTTP {status} {text}" if status is not None else text
        return message, status is not None and 400 <= status < 500

    def _post_resign(self) -> tuple[int | None, str]:
        return self.server.post_resign()

    def seat(self) -> tuple[int | None, Any]:
        return self.server.seat()

    def server_position(self) -> dict[str, Any] | None:
        """The server's position as a stream snapshot (:meth:`.GameServer.position`)."""
        return self.server.position()

    # ---- board side -------------------------------------------------------

    def on_frame(self, _sender: Any, data: bytearray) -> None:
        """Notification callback : keep the latest placement and since when it holds."""
        try:
            placement = decode_board(bytes(data))
        except UnknownPieceCode as exc:
            log.warning("board frame ignored : %s", exc)
            return
        if placement is None:
            return
        if self.observed is None or placement.board_fen() != self.observed.board_fen():
            self.observed = placement
            self.settled_since = time.monotonic()

    def say(self, series: str) -> None:
        """Play a series of :data:`.sounds.SERIES` on the board : ``"ok"``, ``"no"``…

        Logged : the smoke can tell the series sent from the one heard (S2, 08/11).
        """
        log.info("sound %s", series)
        self.sound = sounds.frames(series)

    async def _beep(self, write: Any) -> None:
        if self.sound and (frame := self.sound.pop(0)) is not None:
            await write(frame)

    async def _show(self, write: Any, squares: leds.Frame) -> None:
        if squares != self.leds:
            await write(led_frame(squares))
            self.leds = squares

    async def tick(self, write: Any) -> None:
        """One comparison step ; ``write`` sends a command to the board."""
        if self.game is None or self.observed is None:
            return
        in_sync = self.observed.board_fen() == self.game.board_fen()
        if in_sync:
            if not self.synced and self.game.turn == self.player:
                clock_hold.synced(self, self.game.board_fen())  # my time may run (#164)
            self.synced = True
            self.refused = None
            if self.to_relay is not None:
                log.info("move %s played on the board", self.to_relay)
                self.to_relay = None
        if not self.live:
            await self._end(write, in_sync)
            return
        if in_sync and not self.ready:
            self.ready = True
            self.animation = leds.ready_sweep()
            log.info("the board matches the game : moves count from now on")
            log.info("FEN %s", self.game.fen())
        await follow(self, time.monotonic())
        if await self._resign_gesture(write):
            return
        decision = decide(
            self.game,
            self.player,
            self.observed,
            time.monotonic() - self.settled_since,
            synced=self.synced,
            pending=self.pending is not None,
            refused=self.refused,
            moves=not answers_on_board(self),
        )
        if decision is not None and (decision.leds or decision.move):
            self.animation = []
        if self.animation:
            await self._show(write, self.animation.pop(0))
        elif decision is not None:
            await self._show(write, decision.leds | self.designation.squares)
        if decision is not None and decision.move is not None and time.monotonic() >= self.retry_at:
            await self._play(decision.move)

    async def _end(self, write: Any, in_sync: bool) -> None:
        """Game over : guide to the final position, then a blink, LEDs off, stop.

        A game whose position was never set up on the board (``ready`` still
        False, e.g. an exercise answered in the viewer) has no final position to
        come back to : it ends without guidance. Guiding it would never finish,
        and the lobby gestures stay dead until the Tablier restarts (#258).
        """
        assert self.game is not None and self.observed is not None
        if not self.finished:
            if not (in_sync or self.resign_state == "sent" or not self.ready):
                settled_for = time.monotonic() - self.settled_since
                decision = decide(self.game, self.player, self.observed, settled_for, live=False)
                if decision is not None:
                    await self._show(write, decision.leds)
                return
            self.finished = True
            self.animation += leds.finished_blink()  # after a right answer's blink (#237)
            log.info("game %s over", self.game_id)
        if self.animation:
            await self._show(write, self.animation.pop(0))
            return
        await self._show(write, frozenset())
        self.stopped.set()

    async def _resign_gesture(self, write: Any) -> bool:
        """The player's king off the board : countdown, then resignation. ``True`` while it runs."""
        assert self.game is not None and self.observed is not None
        # The game, or the position before the opponent's move still to relay.
        shown = [self.game] + ([self.relay_from] if self.to_relay and self.relay_from else [])
        if (
            not self.ready  # a king not yet set up is not a resignation (review Junior #75)
            or self.pending is not None
            or self.resign_state == "unavailable"
            or not any(only_king_lifted(b, self.observed, self.player) for b in shown)  # #179
        ):
            self.king_gone_since = None
            return False
        now = time.monotonic()
        if self.king_gone_since is None:
            # In an exercise, a short lift may be the validation (owner, 09/11).
            hold = KING_HOLD_SECONDS if self.exercise is not None else 0.0
            self.king_gone_since = now + hold
            log.info(
                "king off the board : resigning in %.0f s unless it comes back",
                RESIGN_SECONDS + hold,
            )
        elapsed = now - self.king_gone_since
        if elapsed < 0:
            return False
        if self.resign_state is None and elapsed >= RESIGN_SECONDS and now >= self.retry_at:
            await self._resign()
        self.animation = []
        await self._show(
            write, leds.countdown(self.game.king(self.player), elapsed) | self.designation.squares
        )
        return True

    async def _resign(self) -> None:
        status, text = await asyncio.to_thread(self._post_resign)
        if status is not None and (200 <= status < 300 or status == 409):
            # 409 : already over, possibly through an earlier try whose answer
            # was lost (review Admin #73) ; the stream tells the outcome.
            self.resign_state = "sent"
            log.info("resignation sent")
        elif status in (404, 405):
            # Expected with a viewer older than #73 : the game simply goes on
            # (CD, smoke of chessnut-abandon-001).
            log.warning(
                "this viewer cannot take a resignation yet (HTTP %s) : the game goes on", status
            )
            self.resign_state = "unavailable"
        elif status is not None and 300 <= status < 500:
            log.error("resignation refused : HTTP %s %s", status, text)
            self.resign_state = "unavailable"
        else:
            log.warning("resignation not delivered (%s), trying again", text or status)
            self.retry_at = time.monotonic() + RETRY_SECONDS

    async def _play(self, move: chess.Move) -> None:
        if move == self.undelivered and await self._already_played():
            return
        self.pending = move
        assert self.game is not None
        log.info("move read on the board : %s (%s)", move.uci(), self.game.san(move))
        error, refused = await asyncio.to_thread(self.post_move, move.uci())
        self.undelivered = None
        if error is None:
            self.refused = None  # another move went through : nothing left to take back
            if self.exercise is not None:
                self.say("ok")  # the exercise's move found (#186)
            return
        self.pending = None
        if refused:
            log.warning("move %s refused : %s", move.uci(), error)
            self.refused, self.refused_reason = move, error
            if self.exercise is not None:
                self.say("no")  # « Ce n'est pas le coup »
        else:
            log.warning("move %s not delivered (%s), trying again", move.uci(), error)
            self.undelivered = move
            self.retry_at = time.monotonic() + RETRY_SECONDS

    async def _already_played(self) -> bool:
        """Before posting a move again : did the first, unanswered POST land ?

        A move that reached the viewer must not be posted twice (the second
        POST would come back as a refusal). The viewer's position decides,
        compared as positions rather than FEN strings ; if it cannot be read,
        the retry waits.
        """
        server = await asyncio.to_thread(self.server_position)
        if server is None:
            self.retry_at = time.monotonic() + RETRY_SECONDS
            return True
        if self.game is not None and chess.Board(server["fen"]).epd() != self.game.epd():
            log.info("move %s had reached the viewer", self.undelivered)
            self.apply_stream(server)
            return True
        return False

    async def run_board(self) -> None:
        """Connect, stream, reconnect : the board may sleep, lose the link or be unplugged."""
        connect, errors = transport.connector(self.board_transport)
        last_error = None
        while not self.stopped.is_set():
            try:
                async with connect(self.on_frame) as link:
                    log.info("connected to %s", link.name)
                    last_error, self.leds, self.board_link = None, None, "connected"
                    self.link_kind = link.kind
                    try:
                        while link.connected() and not self.stopped.is_set():
                            await self._beep(link.write)
                            await self.tick(link.write)
                            await asyncio.sleep(TICK_SECONDS)
                    finally:
                        self.board_link = "searching"
                        await _switch_off(link.write)
            except transport.BoardNotFound as exc:
                message = f"{exc}, still looking"
                if message != last_error:
                    log.info("%s", message)
                last_error = message
                self.board_link = "searching"  # the error is over (Junior #112)
            except errors() as exc:
                # Once per cause : Bluetooth off repeats every few seconds (Mac smoke, 28/10).
                message = f"board link lost ({exc})"
                if message != last_error:
                    log.warning("%s", message)
                # The same error twice in a row is no passing loss : red light.
                self.board_link = "failing" if message == last_error else "searching"
                last_error = message
            if not self.stopped.is_set():
                await asyncio.sleep(RETRY_SECONDS)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        if task is not None:
            cancel_on_sigterm(loop, task)
        threading.Thread(target=self._follow_stream, args=(loop,), daemon=True).start()
        await asyncio.to_thread(token_light.check, self)
        await asyncio.to_thread(clock_hold.declare, self)
        await asyncio.to_thread(exercise.read, self)
        await (self.run_board() if self.with_board else self.watch_game())

    async def watch_game(self) -> None:
        """No board (#92) : the stream alone, until the game is over."""
        while not self.stopped.is_set():
            if self.game is not None and not self.live:
                self.finished = True
                log.info("game %s over", self.game_id)
                self.stopped.set()
            await asyncio.sleep(TICK_SECONDS)


def cancel_on_sigterm(loop: asyncio.AbstractEventLoop, task: asyncio.Task[Any]) -> None:
    """The launcher stops the bridge with SIGTERM : leave the LEDs off.

    Not available on Windows' event loop (``NotImplementedError``), nor
    outside the main thread, where the app runs the bridge beside its
    window (#78) ; there, Ctrl-C or closing the window stops it.
    """
    try:
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    except (NotImplementedError, RuntimeError, ValueError) as exc:
        log.debug("no SIGTERM handler here (%s)", type(exc).__name__)


async def _switch_off(write: Any) -> None:
    """Best effort on the way out (Ctrl-C, SIGTERM, end of game, lost link)."""
    try:
        await asyncio.wait_for(write(led_frame(())), timeout=2)
    except Exception as exc:  # noqa: BLE001 (the link may already be gone)
        log.debug("LEDs not switched off (%s)", exc)
