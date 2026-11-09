"""Routes for the real-time / live-feed domain of the viewer.

Endpoints covered :

* ``GET /api/games/{game_id}/stream`` — Server-Sent Events on mtime change.
* ``GET /api/clock`` — current clock snapshot (visible to all viewers).
* ``GET /api/engine/live`` — Stockfish eval, behind the engine guard (#206).
* ``GET /api/commentary`` — read JSONL side-car entries.
* ``POST /api/commentary`` — append a JSONL side-car entry.

Routes are mounted at the root of the FastAPI app (no prefix) to
preserve the exact URL contract of the pre-split monolith.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

import chess
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from palamede_chessboard.chess_engine_guard import check_engine_call, log_engine_call
from palamede_chessboard.chessboard_engine import (
    DEFAULT_DEPTH,
    EngineUnavailableError,
    analyze_position,
)
from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameNotFoundError,
    GameState,
    get_commentary,
    post_commentary,
)
from palamede_chessboard.clock_moves import settle_flag
from palamede_chessboard.errors import CommentaryRefusedError
from palamede_chessboard.players.observe import authorize_move
from palamede_chessboard.viewer.models import (
    ACCEPTED_VIEWERS,
    DEPTH_OPTIONS,
    SSE_HEARTBEAT_SECONDS,
    SSE_POLL_INTERVAL_SECONDS,
    CommentaryPayload,
    _game_stream_payload,
    _games_dir,
    _not_found_detail,
)

#: Refusal code of ``POST /api/commentary`` → (HTTP status, message).
_COMMENT_REFUSALS = {
    "UNAUTHENTICATED": (401, "Jeton de joueur requis ou invalide (bouton 🔑)"),
    "NOT_ALLOWED": (403, "Vous n'êtes ni joueur, ni arbitre, ni spectateur de cette partie"),
    "REGISTRY_UNREADABLE": (503, "Registre des joueurs illisible : commentaire refusé"),
}
#: The guarded tool the viewer's live evaluation counts as, in the guard and the log.
ENGINE_TOOL = "chess_analyze_position"
#: ``caller`` of the viewer's lines in the engine log.
CALLER = "viewer"
#: Deepest ``/api/engine/live`` evaluation : the viewer's own menu never asks
#: more, and each call is a Stockfish process (up to its 10 s timeout). Beyond
#: it the call is refused (422), not capped : asking 50 must not get 20 unknowingly.
MAX_DEPTH = max(DEPTH_OPTIONS)
#: 403 of ``/api/engine/live`` : the game, or the position, is in play.
ENGINE_REFUSED = (
    "Engine evaluation is not available while a game is in progress "
    "(fair-play) : wait for the end of the game."
)


def build_realtime_router() -> APIRouter:
    """Return the ``realtime`` ``APIRouter`` ready to mount on ``build_app``."""
    router = APIRouter(tags=["realtime"])

    @router.get("/api/clock")
    def clock(request: Request, game_id: str, viewer: str = "spectator") -> dict:
        # A fallen flag ends the game on time even without a move (#135), when
        # a player of the game or its arbiter reads it with their token.
        try:
            clock_state = settle_flag(game_id, _games_dir(), request.headers)
        except ChessboardError as exc:  # malformed id : 400, not 500
            raise HTTPException(400, str(exc)) from exc
        return {
            "game_id": game_id,
            "viewer": viewer,
            "clock": clock_state.to_dict() if clock_state is not None else None,
        }

    @router.get("/api/engine/live")
    def engine_live(
        game_id: str,
        viewer: str = "spectator",
        fen: str | None = None,
        depth: int = Query(DEFAULT_DEPTH, ge=1, le=MAX_DEPTH),
    ) -> dict:
        try:
            state = GameState.load(game_id, games_dir=_games_dir())
        except GameNotFoundError as exc:
            raise HTTPException(404, _not_found_detail(game_id)) from exc

        if viewer not in ACCEPTED_VIEWERS:
            raise HTTPException(400, f"Unknown viewer role {viewer!r}")
        target_fen = _valid_fen(fen) if fen else state.board.fen()
        _enforce_engine_guard(game_id, target_fen, viewer)
        try:
            analysis = analyze_position(target_fen, depth=depth)
        except EngineUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {
            "game_id": game_id,
            "viewer": viewer,
            "is_over": state.metadata.result not in {"*", ""},
            "analysis": analysis.to_dict(),
        }

    @router.get("/api/games/{game_id}/stream")
    async def game_stream(game_id: str, request: Request) -> StreamingResponse:
        """SSE push: compact snapshot on each PGN mtime change + 15s keepalive.

        See :func:`_sse_event_generator` for the client contract (JSON
        on a single ``data:`` line, SSE comments for keepalive, final
        ``GAME_NOT_FOUND`` error event on disappearance).
        """
        return StreamingResponse(
            _sse_event_generator(game_id, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Disable buffering at proxies that honor this hint (nginx, DSM).
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/api/commentary")
    def commentary_get(game_id: str, ply: int | None = None) -> dict:
        entries = get_commentary(game_id, ply=ply, games_dir=_games_dir())
        return {"game_id": game_id, "count": len(entries), "entries": entries}

    @router.post("/api/commentary")
    def commentary_post(game_id: str, payload: CommentaryPayload, request: Request) -> dict:
        return {"status": "ok", "entry": _post_commentary(game_id, payload, request)}

    return router


def _refuse_unless_commenter(state: GameState, request: Request) -> None:
    """:class:`HTTPException` unless the token may comment on ``state``.

    Same rule as the MCP tool : a player, the arbiter or a declared spectator.
    """
    _, code = authorize_move(request.headers, state, _games_dir(), "chess_post_commentary")
    if code is None:
        return
    status, message = _COMMENT_REFUSALS.get(code, (403, "Commentaire refusé"))
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    raise HTTPException(status, f"{message} ({code})", headers=headers)


def _post_commentary(game_id: str, payload: CommentaryPayload, request: Request) -> dict:
    """The line appended to the game's side-car ; the refusals as HTTP errors."""
    gdir = _games_dir()
    try:
        _refuse_unless_commenter(GameState.load(game_id, games_dir=gdir), request)
        return post_commentary(
            game_id,
            ply=payload.ply,
            source=payload.source,
            text=payload.text,
            games_dir=gdir,
        )
    except GameNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except CommentaryRefusedError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ChessboardError as exc:  # malformed id : 400, not 500
        raise HTTPException(400, str(exc)) from exc


def _valid_fen(fen: str) -> str:
    """``fen`` if it is a position, else 400."""
    try:
        chess.Board(fen)
    except ValueError as exc:
        raise HTTPException(400, "Invalid FEN") from exc
    return fen


def _enforce_engine_guard(game_id: str, fen: str, viewer: str) -> None:
    """403 if the engine guard refuses the evaluation of ``fen`` (#206).

    Refused : a game in progress — whatever ``viewer`` says, which the
    client declares and nobody checks — and a position one of them went
    through, mirror included (``exo.*`` games too). Every call, allowed
    or refused, goes to the engine log with ``caller="viewer"``.
    """
    decision = check_engine_call(_games_dir(), tool=ENGINE_TOOL, game_id=game_id, fen=fen)
    log_engine_call(
        tool=ENGINE_TOOL,
        decision=decision,
        game_id=game_id,
        fen=fen,
        caller=CALLER,
        extra={"viewer": viewer},
    )
    if not decision["allowed"]:
        raise HTTPException(403, ENGINE_REFUSED)


async def _sse_event_generator(game_id: str, request: Request) -> AsyncIterator[str]:
    """Yield SSE frames : one snapshot per PGN mtime change + keepalives.

    Watches ``<GAMES_DIR>/<game_id>.pgn`` mtime because the MCP server
    and the viewer live in different processes (shared disk, not
    memory). Polling every 500ms is imperceptible to humans and cheap
    on I/O.

    Client contract :

    * each event is JSON on a single ``data:`` line (shape in
      :func:`_game_stream_payload`) ;
    * keepalives are SSE comments (``: keepalive\\n\\n``), ignored by
      ``EventSource`` ;
    * on ``GAME_NOT_FOUND``, one error event is emitted and the stream
      closes.
    """
    pgn_path = _games_dir() / f"{game_id}.pgn"
    try:
        state = GameState.load(game_id, games_dir=_games_dir())
    except GameNotFoundError:
        yield f"data: {json.dumps({'error': 'GAME_NOT_FOUND', 'game_id': game_id})}\n\n"
        return
    yield f"data: {json.dumps(_game_stream_payload(state))}\n\n"
    try:
        last_mtime = pgn_path.stat().st_mtime
    except FileNotFoundError:
        return
    last_heartbeat = time.monotonic()
    while True:
        if await request.is_disconnected():
            return
        try:
            mtime = pgn_path.stat().st_mtime
        except FileNotFoundError:
            yield (f"data: {json.dumps({'error': 'GAME_NOT_FOUND', 'game_id': game_id})}" "\n\n")
            return
        if mtime != last_mtime:
            try:
                state = GameState.load(game_id, games_dir=_games_dir())
            except ChessboardError:
                # Transient mid-write state (tmp rename race) — skip.
                await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)
                continue
            yield f"data: {json.dumps(_game_stream_payload(state))}\n\n"
            last_mtime = mtime
            last_heartbeat = time.monotonic()
        elif time.monotonic() - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
            yield ": keepalive\n\n"
            last_heartbeat = time.monotonic()
        await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)
