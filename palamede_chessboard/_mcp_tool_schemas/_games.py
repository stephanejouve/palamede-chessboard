"""MCP tool schemas : game creation, state, listing and Elo.

Part of :data:`palamede_chessboard._mcp_tool_schemas.TOOL_SCHEMAS`
(#46 : split by tool family to stay under the 500-line module budget).
"""

from __future__ import annotations

from mcp.types import Tool

from palamede_chessboard.chess_elo import PROVISIONAL_GAMES

SCHEMAS: list[Tool] = [
    Tool(
        name="chess_create_game",
        description=(
            "Create a new game with player/arbiter/time-control schema. "
            "Returns the initial state. Fails if game_id already exists. "
            "Requires an arbiter : either the ``arbiter`` field is set to "
            "an IA agent id, or a local arbiter daemon must have published "
            "its pidfile in the games dir."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {
                    "type": "string",
                    "description": "Unique short id (^[a-zA-Z0-9_.-]{1,64}$)",
                },
                "white": {"type": "string"},
                "black": {"type": "string"},
                "arbiter": {
                    "type": "string",
                    "description": (
                        "IA agent id responsible for arbitration (any "
                        "non-empty string). If omitted, a running local "
                        "arbiter daemon must have claimed the games dir."
                    ),
                },
                "spectators": {"type": "array", "items": {"type": "string"}},
                "event": {"type": "string"},
                "site": {"type": "string"},
                "round": {"type": "string", "default": "1"},
                "time_control": {
                    "type": "object",
                    "properties": {
                        "format": {"type": "string"},
                        "start_seconds": {"type": "integer"},
                        "increment_seconds": {"type": "integer"},
                    },
                },
                "annotator": {"type": "string"},
                "opening": {"type": "string"},
                "eco": {"type": "string"},
                "starting_fen": {"type": "string", "description": "Optional FEN override"},
                "rated": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Elo perimeter (default true). Pass false for test, "
                        "exercise or friendly games : no ``[Rated]`` header is "
                        "written, so the game stays out of chess_get_elo, "
                        "/api/elo and the matchmaking busy check. Strict JSON "
                        'boolean only (a string like ``"false"`` is refused).'
                    ),
                },
            },
            "required": ["game_id", "white", "black"],
        },
    ),
    Tool(
        name="chess_view_state",
        description="Read-only snapshot of a game (metadata, FEN, ply, history, result).",
        inputSchema={
            "type": "object",
            "properties": {"game_id": {"type": "string"}},
            "required": ["game_id"],
        },
    ),
    Tool(
        name="chess_list_games",
        description=(
            "Enumerate games available on the shared board. Returns a "
            "list of summaries (game_id, white, black, result, ply, "
            "turn, is_game_over, mtime) sorted by mtime desc. Use "
            "``player`` to filter to one agent's games (matches "
            "white OR black, case-insensitive). Use "
            "``active_only=true`` to drop finished games. Malformed "
            "PGN files are skipped. Lets a caller discover its "
            "game_id without touching the filesystem."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "player": {
                    "type": "string",
                    "description": (
                        "Keep only games where white or black matches "
                        "this identifier (case-insensitive)."
                    ),
                },
                "active_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "Keep only games whose result is still ``*``.",
                },
            },
        },
    ),
    Tool(
        name="chess_get_elo",
        description=(
            "Return current Elo ratings computed from the PGNs on the "
            "shared board. By default rebuilds from scratch (source "
            "of truth = PGN headers), then refreshes the "
            "``ratings.json`` cache side-car. Pass ``cache_only=true`` "
            "for a read-only lookup that returns ``null`` if the cache "
            "is missing. Pass ``player`` to narrow the response to one "
            "entry. Pass ``include_history=true`` to get the per-game "
            "update trail (useful for post-mortem). K-factor : 40 "
            f"provisional for the first {PROVISIONAL_GAMES} games, "
            "then 20 (FIDE standard)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "player": {
                    "type": "string",
                    "description": (
                        "Narrow the response to one player's rating. "
                        "Normalised via ``normalize_identity`` (strip "
                        "+ lower + alias table), so ``Leader`` / "
                        "``devleader`` / ``LEADER`` all map to "
                        "``leader``."
                    ),
                },
                "cache_only": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Skip the rebuild and read ``ratings.json`` as-"
                        "is. Returns ``cache_hit=false`` + empty "
                        "``players`` if the cache is missing."
                    ),
                },
                "include_history": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Include the ``history`` list (per-game Elo "
                        "deltas with k-factor and before/after)."
                    ),
                },
            },
        },
    ),
]
