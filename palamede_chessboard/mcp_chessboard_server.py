"""MCP ChessBoard Server — slim transport entry point.

Thin MCP wrapper around :mod:`palamede_chessboard.chessboard_state`. The
heavy lifting lives in three sibling modules split for the palamede
module/function budget :

- ``_mcp_shared`` : helpers, constants, board-facts primitives.
- ``_mcp_tool_schemas`` : the 18 ``Tool()`` schema definitions.
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


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    games_dir = _games_dir()
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


def main() -> None:
    """Entry point for ``python -m palamede_chessboard.mcp_chessboard_server``."""
    asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
