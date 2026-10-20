"""Routes for the ``positions`` domain : board snapshots + static assets.

Three endpoints covered :

* ``GET /api/positions`` — headers + FEN positions walk with ETag/304.
* ``GET /api/board.svg`` — board SVG at a given ply.
* ``GET /api/piece.svg`` — static piece glyph (cacheable immutable).

Routes are mounted at the root of the FastAPI app (no prefix) to
preserve the exact URL contract of the pre-split monolith.
"""

from __future__ import annotations

import hashlib
from typing import Any

import chess
import chess.svg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameNotFoundError,
    GameState,
)
from palamede_chessboard.viewer.models import _games_dir

#: Letter → ``chess.Color`` whitelist for ``/api/piece.svg?color=``.
_PIECE_COLORS = {"w": chess.WHITE, "b": chess.BLACK}
#: Letter → ``chess.PieceType`` whitelist for ``/api/piece.svg?type=``.
_PIECE_TYPES = {
    "p": chess.PAWN,
    "n": chess.KNIGHT,
    "b": chess.BISHOP,
    "r": chess.ROOK,
    "q": chess.QUEEN,
    "k": chess.KING,
}


def build_positions_router() -> APIRouter:
    """Return the ``positions`` ``APIRouter`` ready to mount on ``build_app``."""
    router = APIRouter(tags=["positions"])

    @router.get("/api/piece.svg")
    def piece_svg(color: str, type: str, size: int = 24) -> Response:
        """Return the SVG of a single chess piece (immutable cacheable).

        ``color`` ∈ {"w", "b"}, ``type`` ∈ {"p", "n", "b", "r", "q", "k"}.
        """
        if color not in _PIECE_COLORS or type not in _PIECE_TYPES:
            raise HTTPException(400, "color must be w|b, type must be p|n|b|r|q|k")
        piece = chess.Piece(_PIECE_TYPES[type], _PIECE_COLORS[color])
        svg = chess.svg.piece(piece, size=int(size))
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @router.get("/api/board.svg")
    def board_svg(game_id: str, ply: int = 0, size: int = 400) -> Response:
        try:
            state = GameState.load(game_id, games_dir=_games_dir())
        except GameNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        # Root position, not chess.Board() : a game created from
        # ``starting_fen`` (#36) starts elsewhere.
        board = state.board.root()
        last_move = None
        for i, san in enumerate(state.san_history):
            if i == ply:
                break
            move = board.parse_san(san)
            board.push(move)
            last_move = move
        svg = chess.svg.board(board=board, size=size, lastmove=last_move)
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/positions")
    def positions(game_id: str, request: Request) -> Response:
        """List positions with **ETag/304 conditional GET** (PR #122).

        Polling clients (N viewers) send ``If-None-Match`` ; the server
        compares against an ETag built from ``(ply_count, result)`` and
        returns 304 if unchanged. Drops bandwidth from O(N×size) to
        O(N×hash) on idle games.
        """
        try:
            state = GameState.load(game_id, games_dir=_games_dir())
        except GameNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ChessboardError as exc:
            raise HTTPException(400, str(exc)) from exc

        etag = _compute_positions_etag(game_id, state)
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})

        positions_payload = _walk_positions(state)
        return JSONResponse(
            content={
                "headers": state.metadata.to_pgn_headers(),
                "positions": positions_payload,
                "result": state.metadata.result,
            },
            headers={"ETag": etag, "Cache-Control": "no-cache"},
        )

    return router


def _compute_positions_etag(game_id: str, state: GameState) -> str:
    """Return the quoted ETag for the ``/api/positions`` response.

    Shape : ``"<16-hex-sha256>"`` computed from
    ``<game_id>|<ply_count>|<result>``. Short on purpose — the ETag is
    only used for 304 identity, not integrity.
    """
    etag_raw = f"{game_id}|{len(state.san_history)}|{state.metadata.result}"
    return '"' + hashlib.sha256(etag_raw.encode()).hexdigest()[:16] + '"'


def _walk_positions(state: GameState) -> list[dict[str, Any]]:
    """Replay ``state.san_history`` and emit one dict per ply.

    Factored out of ``positions`` so the handler stays well under the
    80-line budget. Each entry carries ``fen``, played ``san`` (or
    ``None`` for the initial position), ``from``/``to`` square names,
    and the color that moved. Ply 0 is the game's root position, which
    is not the standard start for a game created from ``starting_fen``.
    """
    board = state.board.root()
    out: list[dict[str, Any]] = [
        {"fen": board.fen(), "san": None, "from": None, "to": None, "color": None}
    ]
    for san in state.san_history:
        move = board.parse_san(san)
        color = "w" if board.turn == chess.WHITE else "b"
        from_sq = chess.square_name(move.from_square)
        to_sq = chess.square_name(move.to_square)
        board.push(move)
        out.append({"fen": board.fen(), "san": san, "from": from_sq, "to": to_sq, "color": color})
    return out
