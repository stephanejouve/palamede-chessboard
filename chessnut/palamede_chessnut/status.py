"""What the app's page shows (#78) : a snapshot of the bridge, never its token."""

from __future__ import annotations

import collections
import logging
from typing import TYPE_CHECKING, Any

import chess

from palamede_chessnut.mouse import legal_targets
from palamede_chessnut.moves import differing_squares
from palamede_chessnut.token_light import REFUSING
from palamede_chessnut.token_light import message as token_message

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
    """``"waiting"`` (no game or board yet), ``"on"`` (the board matches), ``"fixing"``.

    ``"none"`` without a board (#92) : nothing to keep in step.
    """
    if not bridge.with_board:
        return "none"
    if bridge.game is None or bridge.observed is None:
        return "waiting"
    if bridge.observed.board_fen() == bridge.game.board_fen():
        return "on"
    return "fixing"


#: French names of the pieces, for the page (review of Stéphane, 28/10).
PIECE_NAMES = {
    chess.PAWN: "pion",
    chess.KNIGHT: "cavalier",
    chess.BISHOP: "fou",
    chess.ROOK: "tour",
    chess.QUEEN: "dame",
    chess.KING: "roi",
}


#: After a chess symbol : drawn as text, not as an emoji (♟ is one on macOS).
TEXT_STYLE = "\ufe0e"


def setup_steps(
    game: chess.BaseBoard, observed: chess.BaseBoard
) -> dict[str, list[dict[str, str]]]:
    """What to do on the board (review of Stéphane and Admin, 28/10), top rank first.

    The page shows ``remove`` first, then ``put`` : the order of the gestures.

    ``put`` : the piece each square needs (symbol, colour, square). ``remove`` :
    the squares to clear, including those holding a wrong piece, which must
    go before the right one is put there.
    """
    put, remove = [], []
    squares = sorted(differing_squares(game, observed), key=lambda s: (-chess.square_rank(s), s))
    for square in squares:
        name = chess.square_name(square)
        if observed.piece_at(square) is not None:
            remove.append({"square": name})
        wanted = game.piece_at(square)
        if wanted is not None:
            colour = "white" if wanted.color == chess.WHITE else "black"
            put.append(
                {"symbol": wanted.unicode_symbol() + TEXT_STYLE, "colour": colour, "square": name}
            )
    return {"put": put, "remove": remove}


def relay_text(bridge: Bridge) -> str | None:
    """« Jouez le coup de l'adversaire : cavalier g8 → f6 », or ``None``.

    :attr:`Bridge.to_relay` is ``g8f6 (Nf6)``, or the SAN alone when the
    move could not be replayed : then the SAN is shown as is. Castling,
    promotion and en passant say what else to move (review Junior #109).
    """
    relay, before = bridge.to_relay, bridge.relay_from
    if not relay:
        return None
    try:
        move = chess.Move.from_uci(relay.split(" ", 1)[0])
    except ValueError:
        move = None
    if move is None or before is None or move not in before.legal_moves:
        return f"Jouez le coup de l'adversaire : {relay}"
    return f"Jouez le coup de l'adversaire : {_said(before, move)}"


def _said(before: chess.Board, move: chess.Move) -> str:
    """The move in plain French, as the player must make it on the board."""
    origin, target = chess.square_name(move.from_square), chess.square_name(move.to_square)
    if before.is_castling(move):
        rook_from, rook_to = ("h", "f") if before.is_kingside_castling(move) else ("a", "d")
        rank = origin[1]
        side = "petit" if rook_from == "h" else "grand"
        return f"{side} roque, roi {origin} → {target} et tour {rook_from}{rank} → {rook_to}{rank}"
    piece = before.piece_at(move.from_square)
    name = PIECE_NAMES[piece.piece_type] if piece is not None else "pièce"
    text = f"{name} {origin} → {target}"
    if move.promotion is not None:
        text += f", promu en {PIECE_NAMES[move.promotion]}"
    if before.is_en_passant(move):
        taken = chess.square_name(
            chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))
        )
        text += f", en passant : retirez le pion {taken}"
    return text


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
    dests, promotions = legal_targets(bridge)
    return {
        "game_id": bridge.game_id,
        "color": "white" if bridge.player == chess.WHITE else "black",
        "fen": game.fen() if game is not None else None,
        "board_fen": observed.board_fen() if observed is not None else None,
        "history": list(bridge.history),
        "live": bridge.live,
        "finished": bridge.finished,
        "to_relay": bridge.to_relay,
        "relay_text": relay_text(bridge),
        "differing": differing,
        "setup": (
            setup_steps(chess.BaseBoard(game.board_fen()), observed)
            if game is not None and observed is not None
            else None
        ),
        # Mouse play (#92) : what the page may play now, empty with a board.
        "mouse": not bridge.with_board,
        # The link in use : with --transport auto, USB or Bluetooth (#110).
        "transport": bridge.link_kind or bridge.board_transport,
        "dests": dests,
        "promotions": promotions,
        "lights": {
            "bluetooth": _bluetooth_light(bridge),
            "server": "on" if bridge.stream_link == "connected" else bridge.stream_link,
            "sync": sync_light(bridge),
            "token": _token_light(bridge),
        },
        # The account name, never the token nor its fingerprint (#78, step A).
        "account": bridge.account,
        "token_message": token_message(bridge),
        "log": list(recent.lines) if recent is not None else [],
    }


def _bluetooth_light(bridge: Bridge) -> str:
    """The board's link (review of Stéphane, 28/10) : ``"ble"`` blue, ``"usb"`` green,
    ``"searching"`` orange (looking, switching), ``"failing"`` red (the same error again),
    ``"closed"`` grey (game over, the bridge has stopped).
    """
    if not bridge.with_board:
        return "none"
    if bridge.stopped.is_set():
        # Game over : the bridge closed the link, grey, not the orange of a search
        # (Mac smoke, 28/10). Stopped on an error (stream, redirect) : red (Admin #113).
        return "closed" if bridge.finished else "failing"
    if bridge.board_link == "connected":
        return "usb" if (bridge.link_kind or bridge.board_transport) == "usb" else "ble"
    return "failing" if bridge.board_link == "failing" else "searching"


def _token_light(bridge: Bridge) -> str:
    """``"on"``, ``"checking"``, ``"unchecked"`` (orange) or ``"refused"`` (red)."""
    return "refused" if bridge.token_light in REFUSING else bridge.token_light
