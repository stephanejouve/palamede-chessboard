"""FastAPI viewer for ``mcp_chessboard_server`` games (Phase C + D).

Migration de ``chess_viewer.py`` v1.1 d'Admin dans le repo outillages,
re-câblé pour lire l'état via les modules ``chessboard_state`` /
``chessboard_clock`` / ``chessboard_engine`` (au lieu de parser le PGN
brut à la volée).

Endpoints :

* ``GET /`` — UI HTML embedded (board SVG live, clocks countdown JS,
  commentary, panneau engine eval avec asymétrie viewer).
* ``GET /api/positions?game_id=<id>`` — headers + positions FEN du PGN.
* ``GET /api/raw?game_id=<id>`` — texte brut PGN.
* ``GET /api/board.svg?game_id=<id>&ply=<int>&size=<px>`` — SVG du board
  à la position ``ply`` (0 = position initiale).
* ``GET /api/health`` — ping.
* ``GET /api/clock?game_id=<id>&viewer=<role>`` — snapshot clock,
  **asymétrie viewer_id** : visible à tous (les joueurs ont droit à voir
  leur temps en live ; le risque fair-play est nul côté horloge).
* ``GET /api/engine/live?game_id=<id>&viewer=<role>&fen=<opt>`` — eval
  Stockfish, **asymétrie viewer_id stricte** : refusée aux ``viewer in
  {player_white, player_black}`` tant que la partie n'est pas terminée.
  ``arbiter``, ``spectator``, ``post_mortem`` ont accès. Si la partie est
  terminée (``result != "*"``), tout viewer y a accès (auto post-mortem).
* ``GET /api/commentary?game_id=<id>&ply=<opt>`` / ``POST`` — relai vers
  la JSONL side-car ``commentary/<game_id>.jsonl``.

Le viewer est mono-instance mais multi-game (un appel = un game_id).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import chess
import chess.svg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from palamede_chessboard.chess_elo import (
    cache_is_stale,
    load_cached_ratings,
    rebuild_and_persist_cache,
)
from palamede_chessboard.chessboard_clock import load_clock
from palamede_chessboard.chessboard_engine import (
    DEFAULT_DEPTH,
    EngineUnavailableError,
    analyze_position,
)
from palamede_chessboard.chessboard_state import (
    GAMES_DIR,
    ChessboardError,
    GameNotFoundError,
    GameState,
    IllegalMoveError,
    get_commentary,
    list_games,
    post_commentary,
)

log = logging.getLogger(__name__)

#: Roles déclarables en tant que ``viewer`` query param.
#:
#: ⚠️ **Honor system** (PR #122 follow-up review #121) : ``viewer`` est
#: déclaré par le client, le serveur ne le vérifie pas (pas de token
#: signé / pas d'auth). Un player malhonnête peut s'auto-déclarer
#: ``spectator`` ou ``arbiter`` pour bypass le 403. La gate effective
#: côté server est purement ``is_over`` : si la partie est en cours,
#: les rôles ``player_white`` / ``player_black`` sont 403 ; sinon
#: tout le monde a accès.
#:
#: Pour transformer en vraie auth (sortir du honor system) : ajouter un
#: token HMAC signé par le MCP server à la création de la partie,
#: distribué aux players via le tool ``chess_create_game``, vérifié
#: côté viewer dans un header ``X-Chessboard-Token``. T-id séparé.
#: Ordered list of viewer roles used to render the role <select> in the UI.
#: Order matters (spectator first = default selection). ``ACCEPTED_VIEWERS``
#: is derived from this for membership checks.
VIEWER_ROLES: tuple[str, ...] = (
    "spectator",
    "arbiter",
    "player_white",
    "player_black",
    "post_mortem",
)
ACCEPTED_VIEWERS: set[str] = set(VIEWER_ROLES)
PLAYER_VIEWERS: set[str] = {"player_white", "player_black"}

#: Depth values exposed in the engine analysis <select> dropdown.
#: ``DEFAULT_DEPTH`` (from ``chessboard_engine``) is pre-selected.
DEPTH_OPTIONS: tuple[int, ...] = (8, 12, 15, 20)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _games_dir() -> Path:
    raw = os.environ.get("MCP_CHESSBOARD_GAMES_DIR", "").strip()
    return Path(raw) if raw else GAMES_DIR


class CommentaryPayload(BaseModel):
    ply: int
    source: str
    text: str


class MovePayload(BaseModel):
    """Payload for ``POST /api/games/<id>/move``.

    Either ``san`` or ``uci`` must be provided. ``uci`` is server-side
    converted to SAN before validation, so promotion suffixes (``e7e8q``)
    are supported without a dedicated UI dialog. ``by`` is audit-only.
    """

    san: str | None = None
    uci: str | None = None
    by: str | None = None


#: SSE heartbeat interval (seconds). DSM reverse proxies typically close
#: idle connections after 60s ; a 15s keepalive stays well under that.
SSE_HEARTBEAT_SECONDS = 15.0
#: Polling interval (seconds) for detecting PGN mtime changes inside the
#: SSE generator. 500ms is imperceptible to humans and cheap on I/O.
SSE_POLL_INTERVAL_SECONDS = 0.5


def _game_stream_payload(state: GameState) -> dict[str, Any]:
    """Compact snapshot pushed on every SSE event.

    Kept small — clients that need the full ``positions`` array can
    still hit ``/api/positions`` on the same game_id ; the SSE event is
    a change notification, not a full replay dump.
    """
    return {
        "game_id": state.metadata.game_id,
        "fen": state.board.fen(),
        "ply": state.board.ply(),
        "turn": "white" if state.board.turn == chess.WHITE else "black",
        "result": state.metadata.result,
        "san_history": list(state.san_history),
        "is_game_over": state.board.is_game_over() or state.metadata.result != "*",
    }


def build_app() -> FastAPI:
    """Return a freshly-instantiated FastAPI viewer app (test-friendly)."""
    app = FastAPI(title="Chess ChessBoard Viewer", version="2.0-phase-c")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/")
    def index(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "chessboard_viewer.html.jinja",
            {
                "viewer_roles": VIEWER_ROLES,
                "depth_options": DEPTH_OPTIONS,
                "default_depth": DEFAULT_DEPTH,
            },
        )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/piece.svg")
    def piece_svg(color: str, type: str, size: int = 24) -> Response:
        """Return the SVG of a single chess piece for use in the captures pane.

        ``color`` ∈ {"w", "b"}, ``type`` ∈ {"p", "n", "b", "r", "q", "k"}.
        Output is cacheable indefinitely — the glyph is immutable.
        """
        color_map = {"w": chess.WHITE, "b": chess.BLACK}
        type_map = {
            "p": chess.PAWN,
            "n": chess.KNIGHT,
            "b": chess.BISHOP,
            "r": chess.ROOK,
            "q": chess.QUEEN,
            "k": chess.KING,
        }
        if color not in color_map or type not in type_map:
            raise HTTPException(400, "color must be w|b, type must be p|n|b|r|q|k")
        piece = chess.Piece(type_map[type], color_map[color])
        svg = chess.svg.piece(piece, size=int(size))
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/api/board.svg")
    def board_svg(game_id: str, ply: int = 0, size: int = 400) -> Response:
        try:
            state = GameState.load(game_id, games_dir=_games_dir())
        except GameNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        board = chess.Board()
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

    @app.get("/api/positions")
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

        etag_raw = f"{game_id}|{len(state.san_history)}|{state.metadata.result}"
        etag = '"' + hashlib.sha256(etag_raw.encode()).hexdigest()[:16] + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})

        board = chess.Board()
        positions_payload: list[dict[str, Any]] = [
            {
                "fen": board.fen(),
                "san": None,
                "from": None,
                "to": None,
                "color": None,
            }
        ]
        for san in state.san_history:
            move = board.parse_san(san)
            color = "w" if board.turn == chess.WHITE else "b"
            from_sq = chess.square_name(move.from_square)
            to_sq = chess.square_name(move.to_square)
            board.push(move)
            positions_payload.append(
                {
                    "fen": board.fen(),
                    "san": san,
                    "from": from_sq,
                    "to": to_sq,
                    "color": color,
                }
            )
        return JSONResponse(
            content={
                "headers": state.metadata.to_pgn_headers(),
                "positions": positions_payload,
                "result": state.metadata.result,
            },
            headers={"ETag": etag, "Cache-Control": "no-cache"},
        )

    @app.get("/api/raw")
    def raw(game_id: str) -> dict:
        try:
            state = GameState.load(game_id, games_dir=_games_dir())
        except GameNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"game_id": game_id, "pgn": state.raw_pgn()}

    @app.get("/api/games")
    def games_endpoint() -> dict:
        """Enumerate ``<games_dir>/*.pgn`` for the viewer's game combobox.

        Returns two lists : ``in_progress`` (games with ``Result "*"``)
        and ``completed`` (finished), each sorted by mtime desc so the
        most recently touched game bubbles to the top. Malformed PGN
        files are silently skipped rather than 500-ing the whole
        listing. Delegates the scan to
        :py:func:`palamede_chessboard.chessboard_state.list_games` so the MCP
        tool ``chess_list_games`` and this endpoint share one source of
        truth.
        """
        entries = list_games(_games_dir())
        in_progress = [e for e in entries if e["result"] == "*"]
        completed = [e for e in entries if e["result"] != "*"]
        return {"in_progress": in_progress, "completed": completed}

    @app.get("/api/clock")
    def clock(game_id: str, viewer: str = "spectator") -> dict:
        clock_state = load_clock(game_id, games_dir=_games_dir())
        return {
            "game_id": game_id,
            "viewer": viewer,
            "clock": clock_state.to_dict() if clock_state is not None else None,
        }

    @app.get("/api/engine/live")
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

        # PR #122 follow-up : the gate is purely ``is_over`` server-side.
        # ``viewer`` is honor-system declarative (cf. ACCEPTED_VIEWERS docstring).
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

        target_fen = fen or state.board.fen()
        try:
            analysis = analyze_position(target_fen, depth=int(depth))
        except EngineUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {
            "game_id": game_id,
            "viewer": viewer,
            "is_over": is_over,
            "analysis": analysis.to_dict(),
        }

    @app.get("/api/games/{game_id}/stream")
    async def game_stream(game_id: str, request: Request) -> StreamingResponse:
        """Server-Sent Events: push a compact game snapshot on each mtime
        change of the game's PGN, plus a keepalive comment every 15s to
        keep DSM's reverse proxy from timing out the idle connection.

        The generator watches ``<GAMES_DIR>/<game_id>.pgn`` mtime rather
        than subscribing to an in-process pubsub because the MCP server
        and the viewer live in different processes (they share
        ``GAMES_DIR`` on disk but not memory). Polling every 500ms is
        imperceptible to humans and cheap on I/O.

        Client contract:
        * Each event is JSON on a single ``data:`` line (see
          ``_game_stream_payload`` for the shape).
        * Keepalives are SSE comments (``: keepalive\\n\\n``) — clients
          should ignore them; ``EventSource`` does automatically.
        * On ``GAME_NOT_FOUND``, one error event is emitted and the
          stream closes.
        """
        pgn_path = _games_dir() / f"{game_id}.pgn"

        async def event_generator():
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
                    yield (
                        f"data: {json.dumps({'error': 'GAME_NOT_FOUND', 'game_id': game_id})}"
                        "\n\n"
                    )
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

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Disable buffering at proxies that honor this hint (nginx, DSM).
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/games/{game_id}/move")
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

        if payload.san:
            san_to_play = payload.san
        else:
            try:
                move = chess.Move.from_uci(payload.uci or "")
            except (ValueError, chess.InvalidMoveError) as exc:
                raise HTTPException(422, f"Invalid UCI {payload.uci!r}: {exc}") from exc
            if move not in state.board.legal_moves:
                raise HTTPException(
                    422,
                    f"Illegal UCI {payload.uci!r} on ply {state.board.ply()}",
                )
            san_to_play = state.board.san(move)

        try:
            move_result = state.play_move(san_to_play, by=payload.by)
        except IllegalMoveError as exc:
            raise HTTPException(422, str(exc)) from exc

        return {
            "status": "ok",
            "move": move_result,
            "state": state.to_state_dict(),
        }

    @app.get("/api/commentary")
    def commentary_get(game_id: str, ply: int | None = None) -> dict:
        entries = get_commentary(game_id, ply=ply, games_dir=_games_dir())
        return {"game_id": game_id, "count": len(entries), "entries": entries}

    @app.post("/api/commentary")
    def commentary_post(game_id: str, payload: CommentaryPayload) -> dict:
        entry = post_commentary(
            game_id,
            ply=payload.ply,
            source=payload.source,
            text=payload.text,
            games_dir=_games_dir(),
        )
        return {"status": "ok", "entry": entry}

    @app.get("/api/elo")
    def elo_endpoint() -> dict:
        """Return the current Elo rating board as JSON.

        Design (CD review PR #295 cycles 1-3, 2026-10-05) :

        * cycle 1 — the cache was only refreshed by ``chess_get_elo`` and
          the CLI ``chess-elo rebuild``, never at game termination.
          :func:`cache_is_stale` fixes that.
        * cycle 2 — race rebuild/écriture + delete detection first
          attempted via ``os.utime(games_dir)`` after save, but that
          raised ``EPERM`` silently when the viewer did not own the
          dir (arbiter viewer, magma-owned ``/Users/Shared/games``),
          triggering a rebuild loop. **Approach dropped.**
        * cycle 3 (current) — no ``utime`` at all. The cache file
          stores ``source_mtime`` (``max(mtime(*.pgn))`` captured
          BEFORE the rebuild) and ``source_pgns`` (sorted stems in
          the dir at rebuild time). :func:`cache_is_stale` compares
          these snapshots to the live state : newer mtime → updates,
          different stem set → add/delete/rename. Immune to filesystem
          ownership. One warning per process on write failure.

        Players are sorted by rating desc, then by games desc as a
        tiebreaker (more games = more confidence at same rating),
        then name asc for stable ordering.
        """
        games_dir = _games_dir()
        if cache_is_stale(games_dir=games_dir):
            board, _ = rebuild_and_persist_cache(games_dir=games_dir)
            source = "rebuild"
        else:
            board = load_cached_ratings(games_dir=games_dir)
            source = "cache"
        players_sorted = [
            {"name": name, "rating": pr.rating, "games": pr.games}
            for name, pr in sorted(
                board.players.items(),
                key=lambda kv: (-kv[1].rating, -kv[1].games, kv[0]),
            )
        ]
        return {
            "source": source,
            "players": players_sorted,
            "history": [u.to_dict() for u in board.history],
        }

    return app


app = build_app()


def _uvicorn_kwargs() -> dict:
    """Build the ``uvicorn.run`` kwargs from env vars (test-friendly).

    Environment variables read :

    * ``MCP_CHESSBOARD_VIEWER_HOST`` (default ``127.0.0.1``).
    * ``MCP_CHESSBOARD_VIEWER_PORT`` (default ``8765``).
    * ``MCP_CHESSBOARD_VIEWER_FORWARDED_IPS`` (default ``127.0.0.1``) :
      liste d'IPs reverse proxy autorisées à pousser ``X-Forwarded-*``.
      Mettre ``*`` pour faire confiance à n'importe quel proxy
      (acceptable en LAN privé fermé), ou lister explicitement les IPs
      reverse proxy (ex. ``192.168.1.78`` pour DSM).
    """
    return {
        "app": "palamede_chessboard.chessboard_viewer:app",
        "host": os.environ.get("MCP_CHESSBOARD_VIEWER_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MCP_CHESSBOARD_VIEWER_PORT", "8765")),
        "reload": False,
        "proxy_headers": True,
        "forwarded_allow_ips": os.environ.get("MCP_CHESSBOARD_VIEWER_FORWARDED_IPS", "127.0.0.1"),
    }


def main() -> None:
    """CLI entry point ``mcp-chessboard-viewer`` (uvicorn launcher).

    Quand le viewer tourne derrière un reverse proxy (ex. DSM
    ``*.alliancejr.eu``), ``proxy_headers=True`` permet à uvicorn de
    lire ``X-Forwarded-Proto`` / ``X-Forwarded-For`` /
    ``X-Forwarded-Host`` et de loguer le vrai client IP au lieu de
    l'IP du proxy.
    """
    import uvicorn

    uvicorn.run(**_uvicorn_kwargs())


if __name__ == "__main__":
    main()
