"""MCP ChessBoard Server — slim transport entry point.

Thin MCP wrapper around :mod:`palamede_chessboard.chessboard_state`. The
heavy lifting lives in three sibling modules split for the palamede
module/function budget :

- ``_mcp_shared`` : helpers, constants, board-facts primitives.
- ``_mcp_tool_schemas`` : the ``Tool()`` schema definitions, one module per tool family.
- ``_mcp_tool_handlers`` : one ``_handle_*`` coroutine per tool.

This module keeps only the ``Server`` instance, the two ``@server.*``
decorated coroutines, the stdio entry point, and backwards-compat
re-exports so existing tests importing
``palamede_chessboard.mcp_chessboard_server`` keep working unchanged.

Status : skeleton. Transport = stdio-only ; no clock enforcement ; no
spectator channel. T-mcp-chessboard-server tracks the remaining work.
"""

from __future__ import annotations

import asyncio
import logging
import os  # re-exported for tests that monkey-patch ``mcp_chessboard_server.os``

from mcp.server import Server
from mcp.types import TextContent, Tool

from palamede_chessboard._mcp_shared import (
    ARBITER_DAEMON_PIDFILE,
    _arbiter_daemon_available,
    _attackers_with_symbols,
    _board_facts_log_path,
    _compute_board_facts,
    _compute_move_facts,
    _games_dir,
    _journal_board_facts_gap,
    _material_summary,
    _mcp_response,
    _require_arbiter_or_daemon,
    _single_move_facts,
    _square_list,
)
from palamede_chessboard._mcp_tool_handlers import TOOL_HANDLERS
from palamede_chessboard._mcp_tool_schemas import TOOL_SCHEMAS
from palamede_chessboard.chessboard_engine import EngineUnavailableError
from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameAlreadyExistsError,
    GameNotFoundError,
    IllegalMoveError,
    InvalidGameStateError,
    StalePlyError,
)
from palamede_chessboard.players.observe import observe

__all__ = [
    "ARBITER_DAEMON_PIDFILE",
    "_arbiter_daemon_available",
    "_attackers_with_symbols",
    "_board_facts_log_path",
    "_compute_board_facts",
    "_compute_move_facts",
    "_games_dir",
    "_journal_board_facts_gap",
    "_material_summary",
    "_mcp_response",
    "_require_arbiter_or_daemon",
    "_single_move_facts",
    "_square_list",
    "call_tool",
    "list_tools",
    "main",
    "os",
    "server",
]

logger = logging.getLogger(__name__)

server = Server("mcp-chessboard")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return list(TOOL_SCHEMAS)


def _request_headers():
    """Headers of the HTTP request carrying this call ; ``None`` over stdio."""
    try:
        request = server.request_context.request
    except LookupError:
        return None
    return getattr(request, "headers", None)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    games_dir = _games_dir()
    headers = _request_headers()
    # #34 step 2 : PALAMEDE_AUTH_MODE=observe journals the verdict, never refuses.
    observe(name, arguments, headers, games_dir, transport="stdio" if headers is None else "http")
    try:
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return _mcp_response({"status": "error", "code": "UNKNOWN_TOOL", "name": name})
        return _mcp_response(await handler(arguments, games_dir))
    except StalePlyError as exc:
        return _mcp_response(
            {
                "status": "error",
                "code": "STALE_PLY",
                "current_ply": exc.current_ply,
                "expected_ply": exc.expected_ply,
            }
        )
    except IllegalMoveError as exc:
        return _mcp_response({"status": "error", "code": "ILLEGAL_MOVE", "reason": str(exc)})
    except GameNotFoundError as exc:
        return _mcp_response({"status": "error", "code": "GAME_NOT_FOUND", "reason": str(exc)})
    except GameAlreadyExistsError as exc:
        return _mcp_response({"status": "error", "code": "GAME_ALREADY_EXISTS", "reason": str(exc)})
    except InvalidGameStateError as exc:
        return _mcp_response({"status": "error", "code": "INVALID_STATE", "reason": str(exc)})
    except ChessboardError as exc:
        return _mcp_response({"status": "error", "code": "CHESSBOARD_ERROR", "reason": str(exc)})
    except EngineUnavailableError as exc:
        return _mcp_response({"status": "error", "code": "ENGINE_UNAVAILABLE", "reason": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return _mcp_response({"status": "error", "code": "INTERNAL", "reason": str(exc)})


async def _run_stdio() -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


#: Transport Streamable HTTP (BT-008, #28) : variables d'environnement lues
#: par ``main`` quand aucun argument n'est passé (cas du conteneur).
TRANSPORT_ENV = "MCP_CHESSBOARD_TRANSPORT"
HTTP_HOST_ENV = "MCP_CHESSBOARD_HTTP_HOST"
HTTP_PORT_ENV = "MCP_CHESSBOARD_HTTP_PORT"
DEFAULT_HTTP_PORT = 8770


def build_http_app():
    """Starlette app serving the MCP server over Streamable HTTP on ``/mcp``.

    **Sans état** (``stateless=True``) : chaque requête porte son contexte, il
    n'y a pas d'identifiant de session côté serveur. Un redémarrage du
    conteneur ou une veille du client ne laissent donc aucune session
    orpheline (critères 4 et 5 de #28) — l'état des parties vit sur disque.
    ``GET /healthz`` répond ``ok`` pour le healthcheck Docker.
    """
    import contextlib

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Mount, Route

    manager = StreamableHTTPSessionManager(app=server, stateless=True)

    async def handle_mcp(scope, receive, send) -> None:
        await manager.handle_request(scope, receive, send)

    async def healthz(_request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    return Starlette(
        routes=[Route("/healthz", healthz), Mount("/mcp", app=handle_mcp)],
        lifespan=lifespan,
    )


def _parse_args(argv: list[str] | None):
    import argparse

    parser = argparse.ArgumentParser(description="palamede-chessboard MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default=os.environ.get(TRANSPORT_ENV, "stdio"),
        help=f"stdio (défaut) ou http = Streamable HTTP (env {TRANSPORT_ENV}).",
    )
    parser.add_argument("--host", default=os.environ.get(HTTP_HOST_ENV, "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get(HTTP_PORT_ENV, DEFAULT_HTTP_PORT))
    )
    args = parser.parse_args(argv)
    # argparse ne valide ``choices`` que pour une valeur passée en ligne de
    # commande, pas pour un ``default`` venu de l'environnement (review Leader
    # #31 : MCP_CHESSBOARD_TRANSPORT=sse partait en http sans erreur).
    if args.transport not in ("stdio", "http"):
        parser.error(f"{TRANSPORT_ENV}={args.transport!r} invalide (attendu : stdio ou http)")
    return args


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m palamede_chessboard.mcp_chessboard_server``."""
    args = _parse_args(argv)
    if args.transport == "stdio":
        asyncio.run(_run_stdio())
        return
    import uvicorn

    uvicorn.run(build_http_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
