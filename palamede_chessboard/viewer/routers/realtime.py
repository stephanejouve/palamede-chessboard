"""Routes for the real-time / live-feed domain of the viewer.

Endpoints covered :

* ``GET /api/games/{game_id}/stream`` — Server-Sent Events on mtime change.
* ``GET /api/clock`` — current clock snapshot (visible to all viewers).
* ``GET /api/engine/live`` — Stockfish eval with viewer honor-system gate.
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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from palamede_chessboard.chessboard_clock import load_clock
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
from palamede_chessboard.viewer.models import (
    ACCEPTED_VIEWERS,
    PLAYER_VIEWERS,
    SSE_HEARTBEAT_SECONDS,
    SSE_POLL_INTERVAL_SECONDS,
    CommentaryPayload,
    _game_stream_payload,
    _games_dir,
)


def build_realtime_router() -> APIRouter:
    """Return the ``realtime`` ``APIRouter`` ready to mount on ``build_app``."""
    router = APIRouter(tags=["realtime"])

    @router.get("/api/clock")
    def clock(game_id: str, viewer: str = "spectator") -> dict:
        clock_state = load_clock(game_id, games_dir=_games_dir())
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
        depth: int = DEFAULT_DEPTH,
    ) -> dict:
        try:
            state = GameState.load(game_id, games_dir=_games_dir())
        except GameNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

        _enforce_engine_gate(state, viewer)

        target_fen = fen or state.board.fen()
        try:
            analysis = analyze_position(target_fen, depth=int(depth))
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
    def commentary_post(game_id: str, payload: CommentaryPayload) -> dict:
        entry = post_commentary(
            game_id,
            ply=payload.ply,
            source=payload.source,
            text=payload.text,
            games_dir=_games_dir(),
        )
        return {"status": "ok", "entry": entry}

    return router


def _enforce_engine_gate(state: GameState, viewer: str) -> None:
    """Raise ``HTTPException`` if the viewer isn't allowed engine access.

    * Unknown role → 400 (not a known viewer bucket).
    * Known player role on a live game → 403 (fair-play honor system).

    Factored out of ``engine_live`` to keep the handler under the
    80-line CD budget.
    """
    if viewer not in ACCEPTED_VIEWERS:
        raise HTTPException(400, f"Unknown viewer role {viewer!r}")
    is_over = state.metadata.result not in {"*", ""}
    if viewer in PLAYER_VIEWERS and not is_over:
        raise HTTPException(
            403,
            "Engine eval hidden from players while the game is live "
            "(fair-play honor system). Wait for game over or use "
            "viewer=spectator|arbiter.",
        )


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
