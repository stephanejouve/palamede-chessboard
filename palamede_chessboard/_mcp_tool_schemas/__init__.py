"""MCP tool schema definitions for the chessboard server.

Extracted from ``mcp_chessboard_server.list_tools`` to keep that function
under the 80-line budget. Order is preserved — the server returns
``list(TOOL_SCHEMAS)`` verbatim.

Split by tool family (#46) to keep each module under 500 lines ; the
concatenation order below is the historical order of the tools.
"""

from __future__ import annotations

from mcp.types import Tool

from palamede_chessboard._mcp_tool_schemas import _analysis, _games, _moves, _players

TOOL_SCHEMAS: list[Tool] = [
    *_games.SCHEMAS,
    *_moves.SCHEMAS,
    *_analysis.SCHEMAS,
    *_players.SCHEMAS,
]
