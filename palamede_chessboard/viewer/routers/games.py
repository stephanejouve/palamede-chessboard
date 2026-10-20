"""Routes for the ``games`` domain : listing, raw PGN export, move push.

Three endpoints covered :

* ``GET /api/games`` — in-progress games and a page of finished ones
  (viewer game selector, #46), with filters and ETag/304.
* ``GET /api/raw`` — raw PGN text for a game.
* ``POST /api/games/{game_id}/move`` — drag&drop move from the viewer UI.

Routes are mounted at the root of the FastAPI app (no prefix) to
preserve the exact URL contract of the pre-split monolith.
"""

from __future__ import annotations

import logging

import chess
from fastapi import APIRouter, HTTPException, Query, Request, Response

from palamede_chessboard.chessboard_state import (
    GameNotFoundError,
    GameState,
    IllegalMoveError,
    list_games,
)
from palamede_chessboard.players.observe import authorize_move
from palamede_chessboard.viewer.games_query import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    etag_matches,
    listing_etag,
    select_games,
)
from palamede_chessboard.viewer.models import MovePayload, _games_dir

log = logging.getLogger(__name__)


def build_games_router() -> APIRouter:
    """Return the ``games`` ``APIRouter`` ready to mount on ``build_app``."""
    router = APIRouter(tags=["games"])

    @router.get("/api/games", response_model=None)
    def games_endpoint(
        request: Request,
        response: Response,
        q: str = Query("", max_length=64),
        player: str = Query("", max_length=64),
        exercises: bool = True,
        limit: int = Query(DEFAULT_LIMIT, ge=0, le=MAX_LIMIT),
        offset: int = Query(0, ge=0),
    ) -> dict | Response:
        """Games for the viewer's selector : all in progress, a page of finished.

        Both lists are sorted by mtime desc. Filters and paging are
        described in :func:`~palamede_chessboard.viewer.games_query.select_games`.
        Malformed PGN files are skipped by
        :py:func:`palamede_chessboard.chessboard_state.list_games`, the
        same scan as the MCP tool ``chess_list_games``. An unchanged
        directory answers **304** from a ``stat``-only ETag.
        """
        gdir = _games_dir()
        etag = listing_etag(gdir, str(request.url.query))
        if etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "no-cache"
        return select_games(
            list_games(gdir),
            q=q,
            player=player,
            exercises=exercises,
            limit=limit,
            offset=offset,
        )

    @router.get("/api/raw")
    def raw(game_id: str) -> dict:
        try:
            state = GameState.load(game_id, games_dir=_games_dir())
        except GameNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"game_id": game_id, "pgn": state.raw_pgn()}

    @router.post("/api/games/{game_id}/move")
    def game_move(game_id: str, payload: MovePayload, request: Request) -> dict:
        """Drag&drop move from the viewer UI ; see :func:`play_from_viewer`."""
        return play_from_viewer(game_id, payload, request)

    return router


def play_from_viewer(game_id: str, payload: MovePayload, request: Request) -> dict:
    """Push a viewer move through ``GameState.play_move()``.

    Same validation path as the MCP tool ``chess_play_move``. ``uci`` is
    converted to SAN server-side so the client can send raw
    ``from_square + to_square [+ promotion]`` without knowing chess
    notation. Returns 422 on illegal moves.

    #34 : the move needs ``Authorization: Bearer <player token>`` and the
    token's player must be the one to move (401 / 403 / 503 otherwise).
    The move is attributed to that player ; ``by`` from the payload is
    ignored.
    """
    if not payload.san and not payload.uci:
        raise HTTPException(400, "One of {san, uci} is required")
    gdir = _games_dir()
    try:
        state = GameState.load(game_id, games_dir=gdir)
    except GameNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    who = _authorized_mover(request, state, gdir)
    san_to_play = _resolve_san(state, payload)

    try:
        move_result = state.play_move(san_to_play, by=who)
    except IllegalMoveError as exc:
        raise HTTPException(422, str(exc)) from exc

    return {
        "status": "ok",
        "move": move_result,
        "state": state.to_state_dict(),
    }


#: Refusal code → (HTTP status, message shown in the viewer).
_MOVE_REFUSALS = {
    "UNAUTHENTICATED": (401, "Jeton de joueur requis ou invalide (bouton 🔑)"),
    "NOT_YOUR_TURN": (403, "Ce n'est pas votre trait"),
    "NOT_A_PLAYER": (403, "Vous ne jouez pas dans cette partie"),
    "REGISTRY_UNREADABLE": (503, "Registre des joueurs illisible : coup refusé"),
}


def _authorized_mover(request: Request, state: GameState, gdir) -> str:
    """Player allowed to move on ``state`` ; :class:`HTTPException` otherwise."""
    who, code = authorize_move(request.headers, state, gdir)
    if code is None and who is not None:
        return who
    status, message = _MOVE_REFUSALS.get(code or "", (403, "Coup refusé"))
    log.warning("viewer move refused on %s : %s (caller %s)", state.metadata.game_id, code, who)
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    raise HTTPException(status, f"{message} ({code})", headers=headers)


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
