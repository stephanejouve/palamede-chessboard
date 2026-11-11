"""``docs/mcp-tools.json`` : the MCP tools, generated from the code, and their HTTP twins (#269).

Same model as :mod:`palamede_chessboard.viewer.openapi_doc` for the HTTP API
(#264) : the committed document must equal what this module builds, or
``tests/mcp/test_mcp_tools_doc.py`` fails. Regenerate after a tool change,
and commit the result :

    poetry run python -m palamede_chessboard.mcp_tools_doc

**Parity** (#328 : one operation, the same rules on every medium). Every
route of ``docs/openapi.json`` names the MCP tool that triggers the same
server operation, or the reason it has none ; every tool names its route, or
the reason it has none. A route or a tool missing from these tables fails
the parity test : a new route decides its MCP twin at once. The reasons
that are open gaps point to their issue (#265 coach, #267 list_games and
positions) and disappear as they are delivered.

The document carries no version : release-please would have to bump it
(``extra-files``, #271) for nothing, the tools do not change at a release.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from palamede_chessboard._mcp_tool_schemas import TOOL_SCHEMAS
from palamede_chessboard.viewer import openapi_routes

DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "mcp-tools.json"

#: HTTP route → the MCP tool that triggers the same server operation.
ROUTE_TOOLS: dict[tuple[str, str], str] = {
    ("get", "/api/clock"): "chess_clock_state",
    ("get", "/api/commentary"): "chess_get_commentary",
    ("post", "/api/commentary"): "chess_post_commentary",
    ("get", "/api/elo"): "chess_get_elo",
    ("get", "/api/engine/live"): "chess_analyze_position",
    ("get", "/api/games"): "chess_list_games",
    ("post", "/api/games"): "chess_create_game",
    ("post", "/api/games/{game_id}/clock/start"): "chess_clock_start",
    ("post", "/api/games/{game_id}/draw/accept"): "chess_agree_draw",
    ("post", "/api/games/{game_id}/draw/decline"): "chess_decline_draw",
    ("post", "/api/games/{game_id}/draw/offer"): "chess_offer_draw",
    ("post", "/api/games/{game_id}/move"): "chess_play_move",
    ("post", "/api/games/{game_id}/resign"): "chess_resign",
    ("get", "/api/games/{game_id}/seat"): "chess_seat",
    ("post", "/api/lessons"): "chess_create_lesson",
    ("get", "/api/me"): "chess_whoami",
    ("get", "/api/raw"): "chess_get_raw_pgn",
}

_COACH = "#265 : le domaine coach (exercices, progression, rapports) n'a aucun outil MCP."
_TABLIER = (
    "Pont du Tablier (eBoard Chessnut) : déclaration et synchronisation d'un échiquier "
    "physique, sans objet pour un agent."
)
_SVG = "Rendu SVG pour le navigateur."

#: HTTP route without an MCP tool → why.
ROUTES_WITHOUT_TOOL: dict[tuple[str, str], str] = {
    ("get", "/"): "Page HTML du viewer.",
    ("get", "/api/board.svg"): _SVG,
    ("get", "/api/piece.svg"): _SVG,
    ("get", "/api/health"): "Santé HTTP du viewer ; le serveur MCP répond sur /healthz.",
    ("get", "/api/games/{game_id}/stream"): (
        "Server-Sent Events ; le MCP est en requête-réponse, un agent relit chess_view_state."
    ),
    ("post", "/api/games/{game_id}/board"): _TABLIER,
    ("post", "/api/games/{game_id}/synced"): _TABLIER,
    ("post", "/api/games/{game_id}/clock/off"): (
        "La partie se joue sans pendule, définitivement (#316) : commande du viewer et du "
        "Tablier. Un agent crée sa partie sans cadence (chess_create_game) ; couper la "
        "pendule d'une partie en cours par MCP reste à décider (#325)."
    ),
    ("get", "/api/positions"): (
        "#267 : pas d'équivalent direct ; chess_view_state donne l'historique SAN et les "
        "positions sont à recalculer."
    ),
    ("get", "/api/coach/exercises"): _COACH,
    ("post", "/api/coach/exercises/{exercise_id}/start"): _COACH,
    ("post", "/api/coach/exercises/{exercise_id}/answer"): _COACH,
    ("get", "/api/coach/progress"): _COACH,
    ("get", "/api/coach/reports/{game_id}"): _COACH,
}

#: MCP tool without an HTTP route → why.
TOOLS_WITHOUT_ROUTE: dict[str, str] = {
    "chess_view_state": (
        "Instantané d'une partie (métadonnées, FEN, ply, historique, résultat) : en HTTP, "
        "GET /api/positions et GET /api/raw le couvrent en deux lectures ; écart noté dans #267."
    ),
    "chess_board_facts": (
        "Faits vérifiables sur une position, sans évaluation, pour la lecture d'un agent ; le "
        "viewer montre l'échiquier et n'a pas de route de faits."
    ),
    "chess_analyze_game": (
        "Analyse de chaque demi-coup d'une partie finie, coups classés ; le viewer n'évalue "
        "que la position courante (GET /api/engine/live)."
    ),
    "chess_get_my_mistakes": (
        "Sous-ensemble des coups d'un joueur classés par chess_analyze_game ; le rapport coach "
        "HTTP (GET /api/coach/reports/{game_id}) est d'une autre nature et n'a pas d'outil : #265."
    ),
    "chess_get_player_preferences": (
        "Préférence max_concurrent du matchmaking entre agents (#38) ; le viewer ne fait pas "
        "de matchmaking."
    ),
    "chess_set_player_preferences": (
        "Préférence max_concurrent du matchmaking entre agents (#38) ; le viewer ne fait pas "
        "de matchmaking."
    ),
}


def route_label(method: str, path: str) -> str:
    """``("post", "/api/games")`` → ``POST /api/games``."""
    return f"{method.upper()} {path}"


def http_twin(tool: str) -> str | None:
    """The route of ``tool``, ``None`` when it has none."""
    return next((route_label(m, p) for (m, p), t in ROUTE_TOOLS.items() if t == tool), None)


def build_doc() -> dict:
    """The document : the tools in their order, each with its schema and its HTTP twin."""
    tools = []
    for tool in TOOL_SCHEMAS:
        entry: dict = {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.inputSchema,
            "http": http_twin(tool.name),
        }
        if entry["http"] is None:
            entry["http_note"] = TOOLS_WITHOUT_ROUTE[tool.name]
        tools.append(entry)
    without = {route_label(m, p): why for (m, p), why in ROUTES_WITHOUT_TOOL.items()}
    return {
        "routes": sorted(route_label(m, p) for m, p in openapi_routes.DOCS),
        "tools": tools,
        "routes_without_tool": dict(sorted(without.items())),
    }


def write(path: Path = DOC_PATH) -> None:
    """Write the document, UTF-8, two-space indent, trailing newline."""
    path.write_text(json.dumps(build_doc(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write()
    print(f"written {DOC_PATH}", file=sys.stderr)
