"""MCP tool schemas : per-player preferences (#38).

Part of :data:`palamede_chessboard._mcp_tool_schemas.TOOL_SCHEMAS`
(#46 : split by tool family to stay under the 500-line module budget).
"""

from __future__ import annotations

from mcp.types import Tool

SCHEMAS: list[Tool] = [
    Tool(
        name="chess_get_player_preferences",
        description=(
            "Read player preferences (issue #38). ``max_concurrent`` = number "
            "of RATED games a player accepts in progress at once before the "
            "matchmaking treats them as busy (default 1). Unrated games never "
            "count. Omit ``player`` to list every stored preference."
        ),
        inputSchema={
            "type": "object",
            "properties": {"player": {"type": "string"}},
        },
    ),
    Tool(
        name="chess_set_player_preferences",
        description=(
            "Set a player's ``max_concurrent`` (integer 1..10). Only affects "
            "the matchmaking busy check : creating games stays unlimited. "
            "Stored outside the PGNs. Identity is not verified yet (registry "
            "of #34) : set only your own preference."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "player": {"type": "string"},
                "max_concurrent": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["player", "max_concurrent"],
        },
    ),
]
