"""Routes for the ``games`` domain : listing, raw PGN export, move push.

Endpoints covered :

* ``GET /api/games`` — in-progress games and a page of finished ones
  (viewer game selector, #46), with filters and ETag/304.
* ``GET /api/raw`` — raw PGN text for a game.
* ``POST /api/games/{game_id}/move`` — drag&drop move from the viewer UI.
* ``GET /api/games/{game_id}/seat`` — whose token, which side (Tablier, #78).
* ``POST /api/games`` — unrated game opened by a player's token (Tablier, #93).

Routes are mounted at the root of the FastAPI app (no prefix) to
preserve the exact URL contract of the pre-split monolith.
"""

from __future__ import annotations

import logging

import chess
from fastapi import APIRouter, HTTPException, Query, Request, Response

from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameNotFoundError,
    GameState,
    IllegalMoveError,
    InvalidGameStateError,
    list_games,
)
from palamede_chessboard.clock_moves import check_game, clock_after_move, game_lock
from palamede_chessboard.players.observe import authorize_move, roles_of
from palamede_chessboard.viewer.games_query import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    etag_matches,
    listing_etag,
    select_games,
)
from palamede_chessboard.viewer.models import MovePayload, _games_dir, _not_found_detail
from palamede_chessboard.viewer.new_game import NewGamePayload, create_from_viewer

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
            raise HTTPException(404, _not_found_detail(game_id)) from exc
        return {"game_id": game_id, "pgn": state.raw_pgn()}

    @router.post("/api/games", status_code=201)
    def game_create(payload: NewGamePayload, request: Request) -> dict:
        """Unrated game opened by the token's player ; see :func:`create_from_viewer`."""
        return create_from_viewer(payload, request, _games_dir())

    @router.post("/api/games/{game_id}/move")
    def game_move(game_id: str, payload: MovePayload, request: Request) -> dict:
        """Drag&drop move from the viewer UI ; see :func:`play_from_viewer`."""
        return play_from_viewer(game_id, payload, request)

    @router.post("/api/games/{game_id}/resign")
    def game_resign(game_id: str, request: Request) -> dict:
        """Resignation of the token's player ; see :func:`resign_from_viewer`."""
        return resign_from_viewer(game_id, request)

    @router.get("/api/games/{game_id}/seat")
    def game_seat(game_id: str, request: Request, response: Response) -> dict:
        """The token's player and their side(s) ; see :func:`seat_of_token`."""
        response.headers["Cache-Control"] = "no-store"
        return seat_of_token(game_id, request)

    return router


def seat_of_token(game_id: str, request: Request) -> dict:
    """Who the token is, and which side they hold in the game ; nothing played.

    Lets the Tablier app check its token at launch instead of at the first
    move (#78, step A). Same refusals as a resignation : 401 for a missing
    or unknown token, 403 for a valid token whose player is not in the game.
    It tells the caller only their own account name.
    """
    gdir = _games_dir()
    try:
        state = GameState.load(game_id, games_dir=gdir)
    except GameNotFoundError as exc:
        raise HTTPException(404, _not_found_detail(game_id)) from exc
    who = _authorized_mover(request, state, gdir, tool="chess_resign")
    roles = roles_of(state)
    colors = [color for color in ("white", "black") if roles[color] == who]
    return {"game_id": game_id, "player": who, "colors": colors}


def resign_from_viewer(game_id: str, request: Request) -> dict:
    """Resign the side of the token's player through ``GameState.resign()``.

    Same rule as the MCP tool ``chess_resign`` : either player may resign,
    whoever is to move. Used by the Chessnut bridge when the player lifts
    their king off the board. 409 when the game is already over.
    """
    gdir = _games_dir()
    try:
        state = GameState.load(game_id, games_dir=gdir)
    except GameNotFoundError as exc:
        raise HTTPException(404, _not_found_detail(game_id)) from exc

    who = _authorized_mover(request, state, gdir, tool="chess_resign")
    roles = roles_of(state)
    if roles["white"] == roles["black"]:
        side = "white" if state.board.turn else "black"
    else:
        side = "white" if who == roles["white"] else "black"

    try:
        result = state.resign(by=side)
    except InvalidGameStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "ok", "resign": result, "state": state.to_state_dict()}


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
    # Checked before the lock : a made-up or malformed id creates nothing
    # (review Leader #136).
    try:
        check_game(game_id, gdir)
    except GameNotFoundError as exc:
        raise HTTPException(404, _not_found_detail(game_id)) from exc
    except ChessboardError as exc:
        raise HTTPException(400, str(exc)) from exc
    # One lock per game, shared with chess_play_move and the clock reads (#135).
    with game_lock(game_id, gdir):
        state = GameState.load(game_id, games_dir=gdir)
        who = _authorized_mover(request, state, gdir)
        san_to_play = _resolve_san(state, payload)
        mover = "white" if state.board.turn else "black"

        try:
            move_result = state.play_move(san_to_play, by=who)
        except IllegalMoveError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Same clock as chess_play_move (#135) : the board, the mouse and Maia
        # play through here.
        clock = clock_after_move(state, mover, gdir)

    return {
        "status": "ok",
        "move": move_result,
        "state": state.to_state_dict(),
        "clock": clock.to_dict() if clock is not None else None,
    }


#: Refusal code → (HTTP status, message shown in the viewer).
_MOVE_REFUSALS = {
    "UNAUTHENTICATED": (401, "Jeton de joueur requis ou invalide (bouton 🔑)"),
    "NOT_YOUR_TURN": (403, "Ce n'est pas votre trait"),
    "NOT_A_PLAYER": (403, "Vous ne jouez pas dans cette partie"),
    "REGISTRY_UNREADABLE": (503, "Registre des joueurs illisible : coup refusé"),
}


def _authorized_mover(
    request: Request, state: GameState, gdir, tool: str = "chess_play_move"
) -> str:
    """Player allowed to act on ``state`` under ``tool``'s rule ; :class:`HTTPException` otherwise."""
    who, code = authorize_move(request.headers, state, gdir, tool)
    if code is None and who is not None:
        return who
    status, message = _MOVE_REFUSALS.get(code or "", (403, "Coup refusé"))
    log.warning("viewer %s refused on %s : %s (caller %s)", tool, state.metadata.game_id, code, who)
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
