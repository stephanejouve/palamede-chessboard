"""Response shapes of the viewer's API, read in the handlers (#264).

The handlers return plain dicts, so FastAPI only knows ``{}`` : these
schemas say what each one really holds. Assembled by
:mod:`palamede_chessboard.viewer.openapi_doc`.
"""

from __future__ import annotations

from typing import Any

_SIDE = {"type": "string", "enum": ["white", "black"]}
_NULLABLE_SIDE = {"anyOf": [_SIDE, {"type": "null"}]}
_RESULT = {
    "type": "string",
    "enum": ["*", "1-0", "0-1", "1/2-1/2"],
    "description": "« * » en cours",
}
_STR_OR_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}


def _obj(properties: dict, required: list[str] | None = None, description: str = "") -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required is not None:
        schema["required"] = required
    if description:
        schema["description"] = description
    return schema


def ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


SCHEMAS: dict[str, dict] = {
    "Error": _obj({"detail": {"type": "string"}}, ["detail"], "Erreur FastAPI (HTTPException)."),
    "GameSummary": _obj(
        {
            "game_id": {"type": "string"},
            "white": {"type": "string"},
            "black": {"type": "string"},
            "result": _RESULT,
            "ply": {"type": "integer", "description": "demi-coups joués"},
            "turn": _SIDE,
            "is_game_over": {"type": "boolean"},
            "mtime": {"type": "number", "description": "date du PGN, secondes depuis l'epoch"},
            "rated": {"type": "boolean"},
            "date": {"type": "string", "description": "en-tête PGN Date (AAAA.MM.JJ)"},
        },
        ["game_id", "white", "black", "result", "ply", "turn", "is_game_over", "mtime"],
        "Une partie de la liste (chessboard_state.list_games).",
    ),
    "GamesList": _obj(
        {
            "in_progress": {"type": "array", "items": ref("GameSummary")},
            "completed": {"type": "array", "items": ref("GameSummary")},
            "completed_total": {"type": "integer"},
            "has_more": {"type": "boolean"},
        },
        ["in_progress", "completed", "completed_total", "has_more"],
        "Toutes les parties en cours (jamais paginées), puis une page des parties finies.",
    ),
    "ClockState": _obj(
        {
            "game_id": {"type": "string"},
            "white_seconds": {"type": "number"},
            "black_seconds": {"type": "number"},
            "increment_seconds": {"type": "integer"},
            "active": _NULLABLE_SIDE,
            "last_move_ts": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            "started": {"type": "boolean"},
            "flag_fall": _NULLABLE_SIDE,
            "board_sides": {
                "type": "array",
                "items": _SIDE,
                "description": "camps joués sur un eBoard (#164)",
            },
            "held": {
                "type": "boolean",
                "description": "temps du camp au trait retenu jusqu'à la synchro (#164)",
            },
            "stale_sides": {
                "type": "array",
                "items": _SIDE,
                "description": "camps d'un pont muet (#164a)",
            },
            "off_by": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "joueur qui a coupé la pendule avant le premier coup (#316)",
            },
            "now_ts": {"type": "number", "description": "heure du serveur, pour le décompte"},
        },
        ["game_id", "white_seconds", "black_seconds", "increment_seconds", "now_ts"],
    ),
    "ClockResponse": _obj(
        {
            "game_id": {"type": "string"},
            "viewer": {"type": "string"},
            "clock": {
                "anyOf": [ref("ClockState"), {"type": "null"}],
                "description": "null : partie sans pendule",
            },
        },
        ["game_id", "viewer", "clock"],
    ),
    "Position": _obj(
        {
            "fen": {"type": "string"},
            "san": _STR_OR_NULL,
            "from": _STR_OR_NULL,
            "to": _STR_OR_NULL,
            "color": {"anyOf": [{"type": "string", "enum": ["w", "b"]}, {"type": "null"}]},
        },
        ["fen", "san", "from", "to", "color"],
        "Une position ; la première (demi-coup 0) n'a ni coup ni couleur.",
    ),
    "PositionsResponse": _obj(
        {
            "headers": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "en-têtes PGN",
            },
            "positions": {"type": "array", "items": ref("Position")},
            "result": _RESULT,
        },
        ["headers", "positions", "result"],
    ),
    "Exercise": _obj(
        {
            "id": {"type": "string"},
            "kind": {"type": "string", "enum": ["attacked", "find_move"]},
            "source_game_id": {"type": "string"},
            "ply": {"type": "integer"},
            "fen": {"type": "string"},
            "side": _SIDE,
            "motif": {
                "type": "string",
                "examples": ["missed_mate", "hanging_piece", "ignored_threat"],
            },
            "state": {"type": "string", "enum": ["proposed", "solved", "failed"]},
            "attempts": {
                "type": "array",
                "items": _obj(
                    {
                        "game_id": {"type": "string"},
                        "ok": {"type": "boolean"},
                        "abandoned": {"type": "boolean"},
                        "at": {"type": "string", "format": "date-time"},
                    }
                ),
            },
            "game_ids": {"type": "array", "items": {"type": "string"}},
            "created_at": {"type": "string", "format": "date-time"},
            "gap_cp": {"type": "integer"},
            "solution_uci": {"type": "string", "description": "seulement une fois résolu"},
            "solution_san": {"type": "string", "description": "seulement une fois résolu"},
            "solution_squares": {
                "type": "array",
                "items": {"type": "string"},
                "description": "seulement une fois résolu",
            },
            "slower_mates": {
                "type": "array",
                "items": {"type": "string"},
                "description": "seulement une fois résolu",
            },
        },
        ["id", "kind", "fen", "side", "motif", "state"],
        "Un exercice tel que son joueur peut le lire : sans solution tant qu'il n'est pas résolu.",
    ),
    "ExercisesResponse": _obj(
        {"player": {"type": "string"}, "exercises": {"type": "array", "items": ref("Exercise")}},
        ["player", "exercises"],
    ),
    "ExerciseStartResponse": _obj(
        {"game_id": {"type": "string"}, "exercise": ref("Exercise")}, ["game_id", "exercise"]
    ),
    "ExerciseAnswerResponse": _obj(
        {"status": {"type": "string", "enum": ["ok"]}, "exercise": ref("Exercise")},
        ["status", "exercise"],
    ),
    "MotifCounts": _obj(
        {
            "exercises": {"type": "integer"},
            "proposed": {"type": "integer"},
            "solved": {"type": "integer"},
            "failed": {"type": "integer"},
            "first_try": _obj({"tried": {"type": "integer"}, "ok": {"type": "integer"}}),
            "solved_after_retry": {"type": "integer"},
            "abandoned_games": {"type": "integer"},
        }
    ),
    "Progress": _obj(
        {
            "player": {"type": "string"},
            "games": {
                "type": "object",
                "description": "erreurs relevées dans les parties, par période et par motif",
            },
            "exercises": {
                "type": "object",
                "additionalProperties": ref("MotifCounts"),
                "description": "par motif",
            },
            "suggestion": {
                "anyOf": [
                    _obj(
                        {
                            "id": {"type": "string"},
                            "motif": {"type": "string"},
                            "kind": {"type": "string"},
                        }
                    ),
                    {"type": "null"},
                ]
            },
            "queue": {
                "type": "array",
                "items": {"type": "string"},
                "description": "ids des exercices, dans l'ordre à jouer",
            },
            "reminder": _obj(
                {
                    "waiting": {"type": "integer"},
                    "last_attempt_at": _STR_OR_NULL,
                    "days_since_last_attempt": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    "due": {"type": "boolean"},
                }
            ),
        },
        ["player", "games", "exercises", "suggestion", "queue", "reminder"],
    ),
    "Me": _obj(
        {
            "player": {"type": "string"},
            "kind": {"type": "string", "description": "type du compte au registre"},
        },
        ["player", "kind"],
    ),
    "Seat": _obj(
        {
            "game_id": {"type": "string"},
            "player": {"type": "string"},
            "colors": {
                "type": "array",
                "items": _SIDE,
                "description": "les deux si le joueur tient les deux camps",
            },
        },
        ["game_id", "player", "colors"],
    ),
    "CreatedGame": _obj(
        {
            "status": {"type": "string", "enum": ["ok"]},
            "game_id": {"type": "string", "description": "tablier.<créateur>.<horodatage>-<hex>"},
            "color": _SIDE,
            "state": {
                "type": "object",
                "description": "état de la partie (GameState.to_state_dict)",
            },
            "clock": {"anyOf": [ref("ClockState"), {"type": "null"}]},
        },
        ["status", "game_id", "color", "state", "clock"],
    ),
    "CreatedLesson": _obj(
        {
            "status": {"type": "string", "enum": ["ok"]},
            "game_id": {
                "type": "string",
                "description": "cours.<thème>.<blancs>-<noirs>.<AAAAMMJJ>-<NN>",
            },
            "state": {
                "type": "object",
                "description": "état de la partie (GameState.to_state_dict)",
            },
        },
        ["status", "game_id", "state"],
    ),
}
