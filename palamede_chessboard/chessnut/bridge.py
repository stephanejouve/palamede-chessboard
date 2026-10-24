"""Bridge between a Chessnut Air+ and a game served by the viewer.

    palamede-chessnut --viewer <viewer URL> --game <game_id> --color white

The player token (the one behind the viewer's 🔑 button) is read from
``PALAMEDE_PLAYER_TOKEN`` or from ``--token-file`` ; it is never logged.

The SSE stream of the game keeps the expected position. Each time the
placement on the board has been still for :data:`SETTLE_SECONDS`,
:func:`decide` compares it with that position :

* same placement → LEDs off ;
* the player is to move and a legal move leads to the placement → the
  move is posted, as the viewer's drag&drop would ;
* otherwise → the squares that differ light up : the opponent's move to
  replicate, a piece to put back, the setup to finish before the start.
  Once the board has caught up with the game (``synced``), this guidance
  waits :data:`GUIDE_SECONDS` on the player's own turn, so that holding a
  piece while thinking does not light its square.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import chess

from palamede_chessboard.chessnut.moves import differing_squares, find_move
from palamede_chessboard.chessnut.protocol import (
    BOARD_UUID,
    COMMAND_UUID,
    ENABLE_REPORTING,
    UnknownPieceCode,
    decode_board,
    led_frame,
)

log = logging.getLogger(__name__)

DEVICE_PREFIX = "Chessnut"
SETTLE_SECONDS = 0.6
GUIDE_SECONDS = 2.0
TICK_SECONDS = 0.1
#: Above the viewer's 15 s SSE keepalive : a silent stream is a dead one.
SSE_READ_TIMEOUT = 45.0
RETRY_SECONDS = 3.0


@dataclass(frozen=True)
class Decision:
    """LEDs to show, and the move to post if the player just played one."""

    leds: frozenset[chess.Square]
    move: chess.Move | None = None


def decide(
    game: chess.Board,
    player: chess.Color,
    observed: chess.BaseBoard,
    settled_for: float,
    *,
    live: bool = True,
    synced: bool = True,
    pending: bool = False,
    refused: chess.Move | None = None,
) -> Decision | None:
    """What to do with a placement still for ``settled_for`` seconds (``None`` : wait).

    ``synced`` : the board has matched the game since its last change ;
    until then the squares to fix light up without delay.
    ``pending`` : a move was posted and the stream has not confirmed it yet.
    ``refused`` : the viewer rejected that move from this position ; it is
    not posted again, its squares light up so that it can be taken back.
    """
    if settled_for < SETTLE_SECONDS:
        return None
    expected = chess.BaseBoard(game.board_fen())
    if observed.board_fen() == expected.board_fen() or pending:
        return Decision(frozenset())
    if live and game.turn == player:
        move = find_move(game, observed)
        if move is not None and move != refused:
            return Decision(frozenset(), move)
        if move is None and synced and settled_for < GUIDE_SECONDS:
            return Decision(frozenset())
    return Decision(frozenset(differing_squares(expected, observed)))


class Bridge:
    """One physical board bound to one game of the viewer."""

    def __init__(self, viewer: str, game_id: str, player: chess.Color, token: str) -> None:
        self.viewer = viewer.rstrip("/")
        self.game_id = game_id
        self.player = player
        self._token = token
        self.game: chess.Board | None = None
        self.live = False
        self.synced = False
        self.observed: chess.BaseBoard | None = None
        self.settled_since = 0.0
        self.pending: chess.Move | None = None
        self.refused: chess.Move | None = None
        self.retry_at = 0.0
        #: Posted move whose outcome is unknown (network error, 5xx).
        self.undelivered: chess.Move | None = None
        self.leds: frozenset[chess.Square] | None = None
        #: Set when the game cannot be followed any more (deleted, unknown id).
        self.stopped = threading.Event()

    # ---- viewer side ------------------------------------------------------

    def apply_stream(self, payload: dict[str, Any]) -> None:
        """New snapshot from the SSE stream (shape : ``_game_stream_payload``)."""
        game = chess.Board(payload["fen"])
        self.live = not payload.get("is_game_over", False)
        if self.game is not None and game.fen() == self.game.fen():
            # Reconnection snapshot : a posted move stays pending until the
            # position actually changes, or it would be posted twice.
            return
        log.info("game %s : ply %s, %s to move", self.game_id, payload["ply"], payload["turn"])
        self.game = game
        self.synced = False
        self.pending = None
        self.undelivered = None

    def _follow_stream(self, loop: asyncio.AbstractEventLoop) -> None:
        """Blocking SSE reader (own thread), reconnecting until :attr:`stopped`."""
        url = f"{self.viewer}/api/games/{quote(self.game_id)}/stream"
        while not self.stopped.is_set():
            try:
                with urllib.request.urlopen(url, timeout=SSE_READ_TIMEOUT) as response:
                    for raw in response:
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
            time.sleep(RETRY_SECONDS)

    def _post_move(self, uci: str) -> tuple[str | None, bool]:
        """Play ``uci`` through the viewer : ``(None, False)``, or ``(message, refused)``.

        ``refused`` only for a 4xx, when the viewer judged the move. A network
        error or a 5xx says nothing about the move, which is tried again.
        """
        request = urllib.request.Request(
            f"{self.viewer}/api/games/{quote(self.game_id)}/move",
            data=json.dumps({"uci": uci}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Not copied onto a redirect (review Admin #71) : check_viewer_url
        # only vouches for the first URL, a 302 may point anywhere.
        request.add_unredirected_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(request, timeout=10):
                return None, False
        except urllib.error.HTTPError as exc:
            message = f"HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}"
            return message, 400 <= exc.code < 500
        except OSError as exc:
            return str(exc), False

    def _server_fen(self) -> str | None:
        """Current position according to the viewer, ``None`` if it cannot be read."""
        url = f"{self.viewer}/api/positions?game_id={quote(self.game_id)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return str(json.load(response)["positions"][-1]["fen"])
        except (OSError, ValueError, KeyError, IndexError) as exc:
            log.warning("position of %s unreadable (%s)", self.game_id, exc)
            return None

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

    async def tick(self, write: Any) -> None:
        """One comparison step ; ``write`` sends a command to the board."""
        if self.game is None or self.observed is None:
            return
        if self.observed.board_fen() == self.game.board_fen():
            self.synced = True
            self.refused = None
        decision = decide(
            self.game,
            self.player,
            self.observed,
            time.monotonic() - self.settled_since,
            live=self.live,
            synced=self.synced,
            pending=self.pending is not None,
            refused=self.refused,
        )
        if decision is None:
            return
        if decision.leds != self.leds:
            await write(led_frame(decision.leds))
            self.leds = decision.leds
        if decision.move is not None and time.monotonic() >= self.retry_at:
            await self._play(decision.move)

    async def _play(self, move: chess.Move) -> None:
        if move == self.undelivered and await self._already_played():
            return
        self.pending = move
        log.info("move read on the board : %s", move.uci())
        error, refused = await asyncio.to_thread(self._post_move, move.uci())
        self.undelivered = None
        if error is None:
            return
        self.pending = None
        if refused:
            log.warning("move %s refused : %s", move.uci(), error)
            self.refused = move
        else:
            log.warning("move %s not delivered (%s), trying again", move.uci(), error)
            self.undelivered = move
            self.retry_at = time.monotonic() + RETRY_SECONDS

    async def _already_played(self) -> bool:
        """Before posting a move again : did the first, unanswered POST land ?

        A move that reached the viewer must not be posted twice (the second
        POST would come back as a refusal). The viewer's position decides ;
        if it cannot be read, the retry waits.
        """
        fen = await asyncio.to_thread(self._server_fen)
        if fen is None:
            self.retry_at = time.monotonic() + RETRY_SECONDS
            return True
        if self.game is not None and fen != self.game.fen():
            log.info("move %s had reached the viewer", self.undelivered)
            self.apply_stream({"fen": fen, "ply": "?", "turn": "?", "is_game_over": not self.live})
            return True
        return False

    async def run_board(self) -> None:
        """Connect, stream, reconnect : the board may sleep or lose the link."""
        from bleak import BleakClient, BleakScanner
        from bleak.exc import BleakError

        while not self.stopped.is_set():
            try:
                device = await BleakScanner.find_device_by_filter(
                    lambda d, _ad: (d.name or "").startswith(DEVICE_PREFIX), timeout=20
                )
                if device is None:
                    log.info("no %s board in range, still looking", DEVICE_PREFIX)
                    continue
                async with BleakClient(device, timeout=20) as client:
                    await client.start_notify(BOARD_UUID, self.on_frame)
                    await client.write_gatt_char(COMMAND_UUID, ENABLE_REPORTING)
                    log.info("connected to %s", device.name)
                    self.leds = None

                    async def write(command: bytes) -> None:
                        await client.write_gatt_char(COMMAND_UUID, command)

                    while client.is_connected and not self.stopped.is_set():
                        await self.tick(write)
                        await asyncio.sleep(TICK_SECONDS)
            except (BleakError, TimeoutError, OSError) as exc:
                log.warning("board link lost (%s)", exc)
            await asyncio.sleep(RETRY_SECONDS)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        threading.Thread(target=self._follow_stream, args=(loop,), daemon=True).start()
        await self.run_board()


def check_viewer_url(url: str) -> None:
    """Refuse a URL that would send the player token in clear over the network."""
    parts = urlsplit(url)
    if parts.scheme == "https" or (
        parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")
    ):
        return
    raise SystemExit(f"{url} : https required (http only on loopback), the player token is sent")


def _read_token(token_file: Path | None) -> str:
    if token_file is not None:
        if token_file.stat().st_mode & 0o077:
            raise SystemExit(f"{token_file} : readable by other accounts, chmod 600 it first")
        return token_file.read_text(encoding="utf-8").strip()
    token = os.environ.get("PALAMEDE_PLAYER_TOKEN", "").strip()
    if not token:
        raise SystemExit("player token missing : PALAMEDE_PLAYER_TOKEN or --token-file")
    return token


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Play a viewer game on a Chessnut Air+ board.")
    parser.add_argument("--viewer", required=True, help="viewer base URL")
    parser.add_argument("--game", required=True, help="game_id")
    parser.add_argument("--color", required=True, choices=["white", "black"])
    parser.add_argument("--token-file", type=Path, help="file holding the player token")
    args = parser.parse_args(argv)
    check_viewer_url(args.viewer)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    player = chess.WHITE if args.color == "white" else chess.BLACK
    bridge = Bridge(args.viewer, args.game, player, _read_token(args.token_file))
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        return
    if bridge.stopped.is_set():
        raise SystemExit(f"game {args.game} cannot be followed, see the log above")
