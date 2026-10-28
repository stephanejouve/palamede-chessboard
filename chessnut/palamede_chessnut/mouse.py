"""Playing with the mouse in the app's window, without a board (#92).

The page asks for a move ; the bridge checks it, then posts it with the
player token, as it posts the moves read on a board. The token never
reaches the page (security frame of Admin on #78, 27/10).
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import chess

from palamede_chessnut.token_light import REFUSING

if TYPE_CHECKING:
    from palamede_chessnut.bridge import Bridge

log = logging.getLogger(__name__)

#: One move from the page at a time : a double click must not post twice.
_posting = threading.Lock()


def playable(bridge: Bridge) -> chess.Board | None:
    """The position the page may play in now, or ``None``."""
    game = bridge.game
    if bridge.with_board or game is None or not bridge.live or bridge.pending is not None:
        return None
    if bridge.token_light in REFUSING or bridge.token_light == "checking":
        return None
    return game if game.turn == bridge.player else None


def legal_targets(bridge: Bridge) -> tuple[dict[str, list[str]], list[str]]:
    """``(dests, promotions)`` for chessground : square → squares, and ``"e7e8"`` pairs."""
    game = playable(bridge)
    if game is None:
        return {}, []
    dests: dict[str, list[str]] = {}
    promotions: set[str] = set()
    for move in game.legal_moves:
        orig, dest = chess.square_name(move.from_square), chess.square_name(move.to_square)
        targets = dests.setdefault(orig, [])
        if dest not in targets:
            targets.append(dest)
        if move.promotion is not None:
            promotions.add(orig + dest)
    return dests, sorted(promotions)


def play_from_page(bridge: Bridge, uci: str) -> tuple[int, str]:
    """A move made in the window : ``(HTTP status, message for the page)``."""
    if not _posting.acquire(blocking=False):
        return 409, "coup précédent en cours d'envoi"
    try:
        return _play(bridge, uci)
    finally:
        _posting.release()


def _play(bridge: Bridge, uci: str) -> tuple[int, str]:
    if bridge.with_board:
        # The board is the reference while it is connected (21.Ra2 of 008b).
        return 409, "un échiquier est connecté : jouez sur l'échiquier"
    game = bridge.game
    if game is None or not bridge.live:
        return 409, "pas de partie en cours"
    if bridge.pending is not None:
        return 409, "coup précédent en attente du serveur"
    if bridge.token_light in REFUSING or bridge.token_light == "checking":
        return 409, "jeton non valide pour cette partie : voir le voyant Jeton"
    if bridge.undelivered is not None:
        stale = _catch_up(bridge, game)
        if stale is not None:
            return stale
    if game.turn != bridge.player:
        return 409, "ce n'est pas votre tour"
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return 400, "coup illisible"
    if move not in game.legal_moves:
        return 400, "coup illégal"
    log.info("move played in the window : %s (%s)", move.uci(), game.san(move))
    bridge.pending = move
    error, refused = bridge.post_move(move.uci())
    if error is None:
        return 200, "ok"
    bridge.pending = None
    log.warning("move %s %s : %s", move.uci(), "refused" if refused else "not delivered", error)
    if refused:
        return 409, "coup refusé par le serveur"
    # The answer may be lost while the move landed : checked before the next one.
    bridge.undelivered = move
    return 502, "coup non transmis : serveur injoignable, réessayez"


def _catch_up(bridge: Bridge, game: chess.Board) -> tuple[int, str] | None:
    """After a lost answer : did that move reach the viewer ? (review Admin #97)

    As on the board path (``_already_played``), the viewer's position
    decides, compared as positions. If it moved, the page is behind : the
    player must see the new position before playing, whoever moved since.
    """
    server = bridge.server_position()
    if server is None:
        return 502, "serveur injoignable : position inconnue, réessayez"
    if chess.Board(server["fen"]).epd() != game.epd():
        log.info("move %s had reached the viewer", bridge.undelivered)
        # From the HTTP thread, as the board path calls it from the loop's : the
        # bridge loop only reads this state without a board (review Junior #97).
        bridge.apply_stream(server)
        return 409, "votre coup précédent était bien arrivé : regardez la position avant de rejouer"
    bridge.undelivered = None
    return None
