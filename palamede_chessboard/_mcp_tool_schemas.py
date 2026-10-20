"""MCP tool schema definitions for the chessboard server.

Extracted from ``mcp_chessboard_server.list_tools`` to keep that function
under the 80-line budget. Order is preserved — the server returns
``list(TOOL_SCHEMAS)`` verbatim.
"""

from __future__ import annotations

from mcp.types import Tool

from palamede_chessboard.chess_elo import PROVISIONAL_GAMES
from palamede_chessboard.chessboard_engine import DEFAULT_DEPTH

TOOL_SCHEMAS: list[Tool] = [
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
