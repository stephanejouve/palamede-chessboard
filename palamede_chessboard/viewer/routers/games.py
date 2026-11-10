"""Routes for the ``games`` domain : listing, raw PGN export, move push.

Endpoints covered :

* ``GET /api/games`` — in-progress games and a page of finished ones
  (viewer game selector, #46), with filters and ETag/304.
* ``GET /api/raw`` — raw PGN text for a game.
* ``POST /api/games/{game_id}/move`` — drag&drop move from the viewer UI.
* ``GET /api/games/{game_id}/seat`` — whose token, which side (Tablier, #78).
* ``POST /api/games/{game_id}/board`` and ``.../synced`` — the relay hold of
  a side played on an eBoard (Tablier, #164).
* ``POST /api/games`` — unrated game opened by a player's token (Tablier, #93).

Routes are mounted at the root of the FastAPI app (no prefix) to
preserve the exact URL contract of the pre-split monolith.
"""

from __future__ import annotations

import logging

import chess
from fastapi import APIRouter, HTTPException, Query, Request, Response

from palamede_chessboard.chess_engine_guard import is_exercise_game
from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameNotFoundError,
    GameState,
    IllegalMoveError,
    InvalidGameStateError,
    list_games,
)
from palamede_chessboard.clock_moves import (
    check_game,
    check_reply_allowed,
    clock_after_move,
    declare_board,
    game_lock,
    release_on_sync,
    start_clock,
    stop_clock_locked,
    switch_clock_off,
)
from palamede_chessboard.coach import play as exercise_play
from palamede_chessboard.coach.store import CoachStoreError
from palamede_chessboard.players import registry
from palamede_chessboard.players.observe import authorize_move, roles_of
from palamede_chessboard.viewer import refusals
from palamede_chessboard.viewer.games_query import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    etag_matches,
    listing_etag,
    select_games,
)
from palamede_chessboard.viewer.models import (
    BoardPayload,
    MovePayload,
    SyncedPayload,
    _games_dir,
    _not_found_detail,
)
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

    _add_draw_routes(router)
    _add_clock_routes(router)

    @router.get("/api/games/{game_id}/seat")
    def game_seat(game_id: str, request: Request, response: Response) -> dict:
        """The token's player and their side(s) ; see :func:`seat_of_token`."""
        response.headers["Cache-Control"] = "no-store"
        return seat_of_token(game_id, request)

    @router.post("/api/games/{game_id}/board")
    def game_board(game_id: str, payload: BoardPayload, request: Request) -> dict:
        return board_of_token(game_id, payload, request)

    @router.post("/api/games/{game_id}/synced")
    def game_synced(game_id: str, payload: SyncedPayload, request: Request) -> dict:
        return synced_of_token(game_id, payload, request)

    return router


def _add_draw_routes(router: APIRouter) -> None:
    @router.post("/api/games/{game_id}/draw/offer")
    def game_draw_offer(game_id: str, request: Request) -> dict:
        """Draw offered by the token's player ; see :func:`draw_from_viewer`."""
        return draw_from_viewer(game_id, request, "offer")

    @router.post("/api/games/{game_id}/draw/accept")
    def game_draw_accept(game_id: str, request: Request) -> dict:
        """The opponent's offer accepted ; see :func:`draw_from_viewer`."""
        return draw_from_viewer(game_id, request, "agree")

    @router.post("/api/games/{game_id}/draw/decline")
    def game_draw_decline(game_id: str, request: Request) -> dict:
        """The opponent's offer declined ; see :func:`draw_from_viewer`."""
        return draw_from_viewer(game_id, request, "decline")


def _add_clock_routes(router: APIRouter) -> None:
    @router.post("/api/games/{game_id}/clock/start")
    def game_clock_start(game_id: str, request: Request) -> dict:
        """The click on the clock ; see :func:`clock_from_viewer`."""
        return clock_from_viewer(game_id, request, "start")

    @router.post("/api/games/{game_id}/clock/off")
    def game_clock_off(game_id: str, request: Request) -> dict:
        """The game played without clock ; see :func:`clock_from_viewer`."""
        return clock_from_viewer(game_id, request, "off")


def clock_from_viewer(game_id: str, request: Request, action: str) -> dict:
    """Start the clock, or switch it off before the first move (#316).

    Same rule as ``chess_clock_start`` : a player of the game or its arbiter,
    under the token. 409 when the game is over, has no clock, or when the
    clock can no longer be set that way.
    """
    gdir = _games_dir()
    who = _authorized_mover(request, _load(game_id, gdir), gdir, tool="chess_clock_start")
    try:
        if action == "start":
            clock = start_clock(game_id, gdir)
        else:
            clock = switch_clock_off(game_id, who, gdir)
    except GameNotFoundError as exc:
        raise HTTPException(404, _not_found_detail(game_id)) from exc
    except InvalidGameStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "ok", "clock": clock.to_dict()}


def board_of_token(game_id: str, payload: BoardPayload, request: Request) -> dict:
    """The token's bridge plays on an eBoard, or no longer does (#164).

    Either player of the game may say it, for their own side(s) only. Only a
    human plays on a board : an agent or a bot declaring one would only win
    the hold at every move (review Admin and Junior #166), 403. Leaving the
    board is open to all.
    """
    gdir = _games_dir()
    seat = seat_of_token(game_id, request)
    if payload.board:
        try:
            kind = registry.load(gdir)["players"].get(seat["player"], {}).get("kind")
        except registry.RegistryError as exc:
            raise HTTPException(503, "Registre des joueurs illisible") from exc
        if kind != "human":
            raise HTTPException(403, "Seul un joueur humain joue sur un eBoard")
    colors = seat["colors"]
    declare_board(game_id, colors, payload.board, gdir)
    return {"status": "ok", "board": payload.board, "colors": colors}


def synced_of_token(game_id: str, payload: SyncedPayload, request: Request) -> dict:
    """The eBoard of the side to move matches the game : its time runs (#164).

    The side to move only (403 otherwise), and only for the current position.
    """
    gdir = _games_dir()
    _authorized_mover(request, _load(game_id, gdir), gdir)
    return {"status": "ok", "released": release_on_sync(game_id, payload.fen, gdir)}


def _load(game_id: str, gdir) -> GameState:
    try:
        return GameState.load(game_id, games_dir=gdir)
    except GameNotFoundError as exc:
        raise HTTPException(404, _not_found_detail(game_id)) from exc


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
    seats = roles_of(state)["seats"]  # a relay team's members included (#280)
    colors = [color for color in ("white", "black") if who in seats[color]]
    return {"game_id": game_id, "player": who, "colors": colors}


def _side_of(state: GameState, who: str) -> str:
    """The side ``who`` acts for ; holding both, the one to move."""
    seats = roles_of(state)["seats"]
    if who in seats["white"] and who in seats["black"]:
        return "white" if state.board.turn else "black"
    return "white" if who in seats["white"] else "black"


def draw_from_viewer(game_id: str, request: Request, action: str) -> dict:
    """Offer, agree or decline a draw : the state machine of the MCP tools (#275).

    Same rule as ``chess_offer_draw`` and its pair : either player, under the
    token. 409 for what the state machine refuses.
    """
    gdir = _games_dir()
    try:
        with game_lock(game_id, gdir):
            state = GameState.load(game_id, games_dir=gdir)
            who = _authorized_mover(request, state, gdir, tool=f"chess_{action}_draw")
            side = _side_of(state, who)
            try:
                result = getattr(state, f"{action}_draw")(by=side)
            except InvalidGameStateError as exc:
                log.info("viewer %s_draw refused on %s : %s", action, game_id, exc)
                raise HTTPException(409, _end_refused(action, state, side)) from exc
            if action == "agree":
                stop_clock_locked(game_id, gdir)  # an agreed draw ends it too (#287)
    except GameNotFoundError as exc:
        raise HTTPException(404, _not_found_detail(game_id)) from exc
    return {"status": "ok", action: result, "state": state.to_state_dict()}


def resign_from_viewer(game_id: str, request: Request) -> dict:
    """Resign the side of the token's player through ``GameState.resign()``.

    Same rule as the MCP tool ``chess_resign`` : either player may resign,
    whoever is to move. Used by the Chessnut bridge when the player lifts
    their king off the board. 409 when the game is already over.
    """
    gdir = _games_dir()
    try:
        # One hold for the end and the clock : a flag falling meanwhile
        # cannot cross the resignation (#289).
        with game_lock(game_id, gdir):
            state = GameState.load(game_id, games_dir=gdir)
            who = _authorized_mover(request, state, gdir, tool="chess_resign")
            side = _side_of(state, who)
            try:
                result = state.resign(by=side)
            except InvalidGameStateError as exc:
                log.info("viewer resign refused on %s : %s", game_id, exc)
                raise HTTPException(409, _end_refused("resign", state, side)) from exc
            stop_clock_locked(game_id, gdir)  # no move ends it : stop it here (#287)
    except GameNotFoundError as exc:
        raise HTTPException(404, _not_found_detail(game_id)) from exc
    if is_exercise_game(game_id):
        try:
            exercise_play.record_abandon(gdir, who, game_id)
        except CoachStoreError:  # the game is resigned all the same
            log.warning("abandon of %s not recorded : unreadable store", game_id)
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
        raise HTTPException(400, refusals.NO_MOVE)
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
            check_reply_allowed(state, gdir)  # the reply waits for the clock (#316)
            move_result = _play(state, who, san_to_play, gdir)
        except IllegalMoveError as exc:
            log.info("viewer move %s refused on %s : %s", san_to_play, game_id, exc)
            detail = refusals.not_possible(refusals.french_san(san_to_play))
            raise HTTPException(422, detail) from exc
        except InvalidGameStateError as exc:  # the game is over (#299), the clock (#316)
            over = refusals.is_over(state.board, state.metadata.result)
            raise HTTPException(409, refusals.GAME_OVER if over else str(exc)) from exc
        # Same clock as chess_play_move (#135) : the board, the mouse and Maia
        # play through here.
        clock = clock_after_move(state, mover, gdir)

    return {
        "status": "ok",
        "move": move_result,
        "state": state.to_state_dict(),
        "clock": clock.to_dict() if clock is not None else None,
    }


def _play(state: GameState, who: str, san: str, gdir) -> dict:
    """The move of ``who`` ; judged first in an exercise game (#95).

    A refused exercise move is a 409 whose ``detail`` the Tablier's bridge
    shows as is (« Ce n'est pas le coup »), the board left unchanged.
    """
    if not is_exercise_game(state.metadata.game_id):
        return state.play_move(san, by=who)
    try:
        return exercise_play.play_move(state, who, san, gdir)
    except exercise_play.ExerciseError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except CoachStoreError as exc:
        log.warning("viewer : exercise store of %s unreadable (%s)", who, exc)
        raise HTTPException(503, "Exercices illisibles : coup refusé") from exc


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
    # The code for programs and tests, never on screen (#322).
    headers = {"X-Refusal-Code": code or "REFUSED"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    raise HTTPException(status, message, headers=headers)


def _end_refused(action: str, state: GameState, side: str) -> str:
    return refusals.end_refused(
        action, state.board, state.metadata.result, state.metadata.draw_offer, side
    )


def _resolve_san(state: GameState, payload: MovePayload) -> str:
    """Return a SAN string from either ``payload.san`` or ``payload.uci``.

    Factored out of ``game_move`` to keep the handler under the 80-line
    CD budget. Raises :class:`HTTPException` 422 on invalid UCI, or on
    legal-UCI-but-illegal-on-this-position mismatch.
    """
    if payload.san:
        return payload.san
    # A board already over (a game set up from a mated FEN) : said as such,
    # not as an impossible move (review Junior #326).
    if refusals.is_over(state.board, state.metadata.result):
        raise HTTPException(409, refusals.GAME_OVER)
    try:
        move = chess.Move.from_uci(payload.uci or "")
    except (ValueError, chess.InvalidMoveError) as exc:
        log.info("viewer : unreadable UCI %r (%s)", payload.uci, exc)
        raise HTTPException(422, refusals.UNREADABLE) from exc
    if move not in state.board.legal_moves:
        # The technical detail in the log ; the player reads the move the French way (#322).
        log.info("viewer : illegal UCI %r at ply %d", payload.uci, state.board.ply())
        raise HTTPException(422, refusals.not_possible(refusals.attempted(state.board, move)))
    return state.board.san(move)
