"""Per-tool handlers for the MCP chessboard server.

Each handler takes ``(arguments: dict, games_dir: Path)`` and returns a
plain ``dict`` payload (not wrapped in TextContent). The slim transport
module wraps the result via ``_mcp_response`` and catches domain
exceptions. Keeping handlers return-dict (not list[TextContent]) keeps
each one under the 80-line budget while preserving a single
exception-catching layer in ``call_tool``.

Split by tool family, like ``_mcp_tool_schemas`` (#46), to keep each module
under 500 lines : the module was at 493 before the tools of #266. The order
below is the historical order of the tools.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from palamede_chessboard._mcp_tool_handlers._analysis import (
    _handle_chess_analyze_game,
    _handle_chess_analyze_position,
    _handle_chess_board_facts,
    _handle_chess_get_my_mistakes,
    _resolve_analyze_fen,
)
from palamede_chessboard._mcp_tool_handlers._games import (
    _handle_chess_create_game,
    _handle_chess_create_lesson,
    _handle_chess_get_elo,
    _handle_chess_list_games,
    _handle_chess_view_state,
)
from palamede_chessboard._mcp_tool_handlers._moves import (
    _handle_chess_agree_draw,
    _handle_chess_clock_start,
    _handle_chess_clock_state,
    _handle_chess_decline_draw,
    _handle_chess_get_commentary,
    _handle_chess_get_raw_pgn,
    _handle_chess_offer_draw,
    _handle_chess_play_move,
    _handle_chess_post_commentary,
    _handle_chess_resign,
)
from palamede_chessboard._mcp_tool_handlers._players import (
    _handle_chess_get_player_preferences,
    _handle_chess_seat,
    _handle_chess_set_player_preferences,
    _handle_chess_whoami,
)

__all__ = ["TOOL_HANDLERS", "_resolve_analyze_fen"]

TOOL_HANDLERS: dict[str, Callable[[dict, Path], Awaitable[dict]]] = {
    "chess_create_game": _handle_chess_create_game,
    "chess_create_lesson": _handle_chess_create_lesson,
    "chess_view_state": _handle_chess_view_state,
    "chess_list_games": _handle_chess_list_games,
    "chess_get_elo": _handle_chess_get_elo,
    "chess_play_move": _handle_chess_play_move,
    "chess_resign": _handle_chess_resign,
    "chess_offer_draw": _handle_chess_offer_draw,
    "chess_agree_draw": _handle_chess_agree_draw,
    "chess_decline_draw": _handle_chess_decline_draw,
    "chess_get_raw_pgn": _handle_chess_get_raw_pgn,
    "chess_post_commentary": _handle_chess_post_commentary,
    "chess_get_commentary": _handle_chess_get_commentary,
    "chess_clock_state": _handle_chess_clock_state,
    "chess_clock_start": _handle_chess_clock_start,
    "chess_board_facts": _handle_chess_board_facts,
    "chess_analyze_position": _handle_chess_analyze_position,
    "chess_analyze_game": _handle_chess_analyze_game,
    "chess_get_my_mistakes": _handle_chess_get_my_mistakes,
    "chess_get_player_preferences": _handle_chess_get_player_preferences,
    "chess_set_player_preferences": _handle_chess_set_player_preferences,
    "chess_whoami": _handle_chess_whoami,
    "chess_seat": _handle_chess_seat,
}
