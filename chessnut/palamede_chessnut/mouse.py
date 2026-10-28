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
    error, refused = bridge._post_move(move.uci())
    if error is None:
        return 200, "ok"
    bridge.pending = None
    log.warning("move %s %s : %s", move.uci(), "refused" if refused else "not delivered", error)
    if refused:
        return 409, "coup refusé par le serveur"
    return 502, "coup non transmis : serveur injoignable, réessayez"
