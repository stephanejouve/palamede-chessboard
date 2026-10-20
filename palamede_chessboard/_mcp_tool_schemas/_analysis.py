"""MCP tool schemas : board facts and engine analyses (engine-guarded).

Part of :data:`palamede_chessboard._mcp_tool_schemas.TOOL_SCHEMAS`
(#46 : split by tool family to stay under the 500-line module budget).
"""

from __future__ import annotations

from mcp.types import Tool

from palamede_chessboard.chessboard_engine import DEFAULT_DEPTH

SCHEMAS: list[Tool] = [
    Tool(
        name="chess_board_facts",
        description=(
            "Faits vérifiables sur une position d'échecs — SANS évaluation. "
            "\n\n"
            "**Usage recommandé en trois temps (CD 2026-09-30)** :\n"
            "1. **Prédis** — écris ta lecture dans ``expected`` avant "
            "l'appel (ex : « ma dame en e6 est défendue, rien ne la "
            "menace »). L'outil ne l'interprète pas, il te la renvoie "
            "telle quelle à côté des faits.\n"
            "2. **Vérifie** — compare ta prédiction aux faits retournés. "
            "Si un écart saute aux yeux (pièce en prise que tu croyais "
            "défendue, clouage manqué…), c'est une leçon.\n"
            "3. **Note la leçon dans ta mémoire** — l'outil produit des "
            "leçons, il ne les remplace pas. Nos poids ne bougent pas "
            "d'une partie à l'autre : ce qui s'accumule c'est la mémoire "
            "écrite. Le journal ``board_facts_log.jsonl`` donne la "
            "mesure : le taux d'écarts par partie devrait baisser si les "
            "leçons servent.\n"
            "\n"
            "**Contenu retourné** : pour chaque pièce, case + attaquants "
            "et défenseurs (listes de {square, piece}, pas des "
            "compteurs). Pièces en prise : attaquées et non-défendues OU "
            "attaquées par une pièce de moindre valeur. Clouages absolus "
            "(contre le roi). Coups d'échec disponibles pour le camp au "
            "trait, en SAN. Avec ``move_san`` : simule le(s) coup(s) sur "
            "une COPIE du board — case d'arrivée, pièces nouvellement en "
            "prise (y compris par découverte). "
            "\n\n"
            "**Note ray-X** : ``board.attackers()`` ne voit pas les "
            "pièces alignées derrière (batteries) — les attackers listés "
            "ne sont pas une entrée SEE. "
            "\n\n"
            "**Interdit** : score, meilleur coup, ranking, variante, "
            "Stockfish."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {
                    "type": "string",
                    "description": "Charge la position courante du game (exclusif avec fen)",
                },
                "fen": {
                    "type": "string",
                    "description": "FEN libre (exclusif avec game_id)",
                },
                "move_san": {
                    "description": (
                        "Optionnel — string OU array de SAN. Simule le(s) "
                        "coup(s) sur une COPIE du board (le game n'est PAS "
                        "modifié), et renvoie les faits après chaque "
                        "demi-coup : case d'arrivée, pièces nouvellement "
                        "en prise, faits complets post-move, matériel. "
                        "Séquence stoppée au 1er coup illégal avec "
                        "``stopped_at`` + ``error``, les coups légaux "
                        "précédents sont conservés dans ``sequence``."
                    ),
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "expected": {
                    "type": "string",
                    "description": (
                        "Optionnel — ta prédiction en texte libre AVANT "
                        "l'appel (ex : « dame en e6 défendue, rien ne la "
                        "menace »). L'outil ne l'interprète pas : il te "
                        "la renvoie telle quelle dans la réponse pour que "
                        "l'écart avec les faits saute aux yeux. Si "
                        "``game_id`` est fourni, une ligne est ajoutée à "
                        "``~/.local/share/outillages/board_facts_log.jsonl`` "
                        "(journal per-user sous le home du process MCP — "
                        "chacun le sien, pas de partage cross-user) : ply, "
                        "side_to_move, expected, résumé pièces en "
                        "prise/clouages. Pas de verdict automatique, "
                        "c'est toi qui juges l'écart. Override via env "
                        "``OUTILLAGES_BOARD_FACTS_LOG``."
                    ),
                },
            },
        },
    ),
    Tool(
        name="chess_analyze_position",
        description=(
            "Run local Stockfish on a position (game current FEN, or a "
            "specific ply, or a free-form FEN). Returns score in cp, "
            "best move, and principal variation. Refused on positions "
            "that belong to an in-progress game — call is logged in "
            "``~/.local/share/outillages/engine_calls_log.jsonl`` "
            "either way."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string", "description": "Optional if fen provided"},
                "ply": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Optional position selector",
                },
                "fen": {"type": "string", "description": "Optional FEN override"},
                "depth": {"type": "integer", "minimum": 1, "default": DEFAULT_DEPTH},
                "caller": {
                    "type": "string",
                    "description": "Agent identifier for the audit log (optional).",
                },
            },
        },
    ),
    Tool(
        name="chess_analyze_game",
        description=(
            "Walk every ply of a game, evaluate before/after, classify "
            "each move as blunder/mistake/inaccuracy/good. Refused on "
            "in-progress games — audit entry written in all cases."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "depth": {"type": "integer", "minimum": 1, "default": DEFAULT_DEPTH},
                "caller": {
                    "type": "string",
                    "description": "Agent identifier for the audit log (optional).",
                },
            },
            "required": ["game_id"],
        },
    ),
    Tool(
        name="chess_get_my_mistakes",
        description=(
            "Return the subset of one player's moves classified above "
            "min_classification (default inaccuracy). Refused on "
            "in-progress games — audit entry written in all cases."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "player": {"type": "string", "enum": ["white", "black"]},
                "min_classification": {
                    "type": "string",
                    "enum": ["inaccuracy", "mistake", "blunder"],
                    "default": "inaccuracy",
                },
                "depth": {"type": "integer", "minimum": 1, "default": DEFAULT_DEPTH},
                "caller": {
                    "type": "string",
                    "description": "Agent identifier for the audit log (optional).",
                },
            },
            "required": ["game_id", "player"],
        },
    ),
]
