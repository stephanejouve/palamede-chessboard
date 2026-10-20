"""Règles de découpage du dépôt (#5, #46), vérifiées en CI.

Module ≤ 500 lignes, fonction ≤ 80 lignes (fonction entière, décorateurs
et docstring compris). Jusqu'ici ces seuils n'étaient vérifiés qu'en
revue : #40 a fait passer ``_mcp_tool_schemas.py`` à 512 lignes sans que
rien ne le signale.
"""

from __future__ import annotations

import ast
from pathlib import Path

import palamede_chessboard
from palamede_chessboard._mcp_tool_schemas import TOOL_SCHEMAS

PACKAGE = Path(palamede_chessboard.__file__).parent
MODULE_MAX_LINES = 500
FUNCTION_MAX_LINES = 80


def _modules() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def test_modules_stay_under_budget() -> None:
    over = {
        str(p.relative_to(PACKAGE)): n
        for p in _modules()
        if (n := len(p.read_text(encoding="utf-8").splitlines())) > MODULE_MAX_LINES
    }
    assert over == {}


def test_functions_stay_under_budget() -> None:
    over = []
    for path in _modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                length = (node.end_lineno or node.lineno) - start + 1
                if length > FUNCTION_MAX_LINES:
                    over.append(f"{path.relative_to(PACKAGE)}:{node.name} ({length})")
    assert over == []


def test_tool_order_is_unchanged() -> None:
    """The split of ``_mcp_tool_schemas`` (#46) keeps the historical order."""
    assert [t.name for t in TOOL_SCHEMAS] == [
        "chess_create_game",
        "chess_view_state",
        "chess_list_games",
        "chess_get_elo",
        "chess_play_move",
        "chess_resign",
        "chess_offer_draw",
        "chess_agree_draw",
        "chess_decline_draw",
        "chess_get_raw_pgn",
        "chess_post_commentary",
        "chess_get_commentary",
        "chess_clock_state",
        "chess_clock_start",
        "chess_board_facts",
        "chess_analyze_position",
        "chess_analyze_game",
        "chess_get_my_mistakes",
        "chess_get_player_preferences",
        "chess_set_player_preferences",
    ]
