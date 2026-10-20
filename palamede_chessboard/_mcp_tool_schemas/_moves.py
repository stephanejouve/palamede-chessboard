"""MCP tool schemas : moves, resign, draws, raw PGN, commentary and clock.

Part of :data:`palamede_chessboard._mcp_tool_schemas.TOOL_SCHEMAS`
(#46 : split by tool family to stay under the 500-line module budget).
"""

from __future__ import annotations

from mcp.types import Tool

SCHEMAS: list[Tool] = [
    Tool(
        name="chess_play_move",
        description=(
            "Validate a SAN move against the live board and persist the PGN. "
            "Illegal/ambiguous SAN is rejected. Returns the new state. "
            "If ``expected_ply`` is supplied and the board has already "
            "advanced past it (concurrent writer), the call is rejected "
            "with ``code=STALE_PLY`` BEFORE the move is applied — the "
            "caller should re-read state and retry."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "move_san": {"type": "string", "description": "e.g. e4, Nf3, exd5, O-O, e8=Q+"},
                "by": {"type": "string", "description": "Player identifier (audit only)"},
                "expected_ply": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Idempotence guard. If provided and ≠ current "
                        "board.ply(), returns STALE_PLY without pushing."
                    ),
                },
            },
            "required": ["game_id", "move_san"],
        },
    ),
    Tool(
        name="chess_resign",
        description=(
            "Resign the game on behalf of one side. Updates result "
            "(0-1 if white resigns, 1-0 if black resigns), writes a "
            "termination note in the PGN, and clears any pending draw "
            "offer. Refused if the game is already over."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "by": {
                    "type": "string",
                    "description": "'white' | 'black' or player name matching White/Black",
                },
            },
            "required": ["game_id", "by"],
        },
    ),
    Tool(
        name="chess_offer_draw",
        description=(
            "Register a draw offer from one side, awaiting the other "
            "side's response (agree_draw or decline_draw). Idempotent "
            "when re-offering from the same side. Refused if the "
            "opposite side already has a pending offer."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "by": {
                    "type": "string",
                    "description": "'white' | 'black' or player name matching White/Black",
                },
            },
            "required": ["game_id", "by"],
        },
    ),
    Tool(
        name="chess_agree_draw",
        description=(
            "Accept a pending draw offer from the opposite side, "
            "ending the game with result 1/2-1/2. Refused if no "
            "offer is pending, or if the accepting side made the offer."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "by": {
                    "type": "string",
                    "description": "'white' | 'black' or player name matching White/Black",
                },
            },
            "required": ["game_id", "by"],
        },
    ),
    Tool(
        name="chess_decline_draw",
        description=(
            "Decline a pending draw offer from the opposite side. "
            "Clears the offer without ending the game. Playing a "
            "move implicitly declines too (see chess_play_move)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "by": {
                    "type": "string",
                    "description": "'white' | 'black' or player name matching White/Black",
                },
            },
            "required": ["game_id", "by"],
        },
    ),
    Tool(
        name="chess_get_raw_pgn",
        description="Return the raw PGN file content for a game.",
        inputSchema={
            "type": "object",
            "properties": {"game_id": {"type": "string"}},
            "required": ["game_id"],
        },
    ),
    Tool(
        name="chess_post_commentary",
        description="Append a ply-indexed commentary line to the per-game side-car log.",
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "ply": {"type": "integer", "minimum": 0},
                "source": {"type": "string", "description": "Free-form id (e.g. arbiter name)"},
                "text": {"type": "string"},
            },
            "required": ["game_id", "ply", "source", "text"],
        },
    ),
    Tool(
        name="chess_get_commentary",
        description="Return commentary entries for a game (all or filtered by ply).",
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "ply": {"type": "integer", "minimum": 0},
            },
            "required": ["game_id"],
        },
    ),
    Tool(
        name="chess_clock_state",
        description=(
            "Return the clock snapshot for a game "
            "(white/black seconds, active side, flag_fall flag)."
        ),
        inputSchema={
            "type": "object",
            "properties": {"game_id": {"type": "string"}},
            "required": ["game_id"],
        },
    ),
    Tool(
        name="chess_clock_start",
        description=(
            "Start the clock for a game (active=white, last_move_ts=now). "
            "No-op if the game has no clock (TimeControl=unrated)."
        ),
        inputSchema={
            "type": "object",
            "properties": {"game_id": {"type": "string"}},
            "required": ["game_id"],
        },
    ),
]
