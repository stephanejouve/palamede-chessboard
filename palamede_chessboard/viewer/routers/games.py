"""Routes for the ``games`` domain : listing, raw PGN export, move push.

Three endpoints covered :

* ``GET /api/games`` — list in-progress + completed games (viewer combobox).
* ``GET /api/raw`` — raw PGN text for a game.
* ``POST /api/games/{game_id}/move`` — drag&drop move from the viewer UI.

Routes are mounted at the root of the FastAPI app (no prefix) to
preserve the exact URL contract of the pre-split monolith.
"""

from __future__ import annotations

import chess
from fastapi import APIRouter, HTTPException

from palamede_chessboard.chessboard_state import (
    GameNotFoundError,
    GameState,
    IllegalMoveError,
    list_games,
)
from palamede_chessboard.viewer.models import MovePayload, _games_dir


def build_games_router() -> APIRouter:
    """Return the ``games`` ``APIRouter`` ready to mount on ``build_app``."""
    router = APIRouter(tags=["games"])

    @router.get("/api/games")
    def games_endpoint() -> dict:
        """Enumerate ``<games_dir>/*.pgn`` for the viewer's game combobox.

        Returns two lists : ``in_progress`` (games with ``Result "*"``)
        and ``completed`` (finished), each sorted by mtime desc so the
        most recently touched game bubbles to the top. Malformed PGN
        files are silently skipped rather than 500-ing the whole
        listing. Delegates the scan to
        :py:func:`palamede_chessboard.chessboard_state.list_games` so
        the MCP tool ``chess_list_games`` and this endpoint share one
        source of truth.
        """
        entries = list_games(_games_dir())
        in_progress = [e for e in entries if e["result"] == "*"]
        completed = [e for e in entries if e["result"] != "*"]
        return {"in_progress": in_progress, "completed": completed}

    @router.get("/api/raw")
    def raw(game_id: str) -> dict:
        try:
            state = GameState.load(game_id, games_dir=_games_dir())
        except GameNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"game_id": game_id, "pgn": state.raw_pgn()}

    @router.post("/api/games/{game_id}/move")
    def game_move(game_id: str, payload: MovePayload) -> dict:
        """Accept a drag&drop move from the viewer UI and push it through
        ``GameState.play_move()`` (same validation path as the MCP tool
        ``chess_play_move``). ``uci`` is converted to SAN server-side so
        the client can send raw ``from_square + to_square [+ promotion]``
        without knowing chess notation. Returns 422 on illegal moves.
        """
        if not payload.san and not payload.uci:
            raise HTTPException(400, "One of {san, uci} is required")
        try:
            state = GameState.load(game_id, games_dir=_games_dir())
        except GameNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

        san_to_play = _resolve_san(state, payload)

        try:
            move_result = state.play_move(san_to_play, by=payload.by)
        except IllegalMoveError as exc:
            raise HTTPException(422, str(exc)) from exc

        return {
            "status": "ok",
            "move": move_result,
            "state": state.to_state_dict(),
        }

    return router


def _resolve_san(state: GameState, payload: MovePayload) -> str:
    """Return a SAN string from either ``payload.san`` or ``payload.uci``.

    Factored out of ``game_move`` to keep the handler under the 80-line
    CD budget. Raises :class:`HTTPException` 422 on invalid UCI, or on
    legal-UCI-but-illegal-on-this-position mismatch.
    """
    if payload.san:
        return payload.san
    try:
        move = chess.Move.from_uci(payload.uci or "")
    except (ValueError, chess.InvalidMoveError) as exc:
        raise HTTPException(422, f"Invalid UCI {payload.uci!r}: {exc}") from exc
    if move not in state.board.legal_moves:
        raise HTTPException(
            422,
            f"Illegal UCI {payload.uci!r} on ply {state.board.ply()}",
        )
    return state.board.san(move)
