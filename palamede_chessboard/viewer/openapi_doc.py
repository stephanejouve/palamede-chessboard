"""OpenAPI description of the viewer's HTTP API, versioned in ``docs/openapi.json``.

FastAPI generates the skeleton from the code (``build_app().openapi()`` :
routes, parameters, request bodies). This module adds what the code does
not say : the bearer token, the error codes, the response shapes (the
handlers return plain dicts, so FastAPI only knows ``{}``), the content
types of the non-JSON routes, the 304 of the conditional GETs, and a French
summary per route.

Regenerate after an API change, and commit the result :

    poetry run python -m palamede_chessboard.viewer.openapi_doc

``tests/viewer/test_openapi_doc.py`` fails while ``docs/openapi.json`` differs
from what this module builds, and when a route has no annotation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from palamede_chessboard import __version__
from palamede_chessboard.viewer.app import build_app
from palamede_chessboard.viewer.models import VIEWER_ROLES

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"

SERVER = "https://chess.alliancejr.eu"

DESCRIPTION = (
    "API HTTP du viewer palamède, servie sur chess.alliancejr.eu. Générée depuis le code "
    "(FastAPI, `build_app().openapi()`), puis annotée par "
    "`palamede_chessboard/viewer/openapi_doc.py` : authentification, codes d'erreur, formes "
    "des réponses et types de contenu.\n\n"
    "**Authentification** : `Authorization: Bearer plm_…`, jeton d'un joueur du registre. "
    "Les lectures (parties, positions, horloge, flux) sont publiques ; les actions d'un joueur "
    "exigent son jeton. Sans jeton ou avec un jeton inconnu : 401, la même réponse quelle que "
    "soit la raison (absent, révoqué, inconnu). Jeton valide mais pas le bon joueur : 403. "
    "Registre ou données illisibles : 503.\n\n"
    '**Erreurs** : corps `{"detail": "…"}`, message en français.\n\n'
    "**Hors périmètre** : le serveur MCP (`/mcp`, outils `chess_*`, `/healthz`), qui n'est pas "
    "une API REST, et les fichiers statiques (`/assets`, `/vendor`, favicon)."
)

TAGS = ["parties", "Tablier", "coach", "joueurs", "analyse", "rendu", "viewer"]

# --------------------------------------------------------------------------
# Response shapes, read in the handlers (the code returns plain dicts)
# --------------------------------------------------------------------------

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


def _ref(name: str) -> dict:
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
            "in_progress": {"type": "array", "items": _ref("GameSummary")},
            "completed": {"type": "array", "items": _ref("GameSummary")},
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
            "now_ts": {"type": "number", "description": "heure du serveur, pour le décompte"},
        },
        ["game_id", "white_seconds", "black_seconds", "increment_seconds", "now_ts"],
    ),
    "ClockResponse": _obj(
        {
            "game_id": {"type": "string"},
            "viewer": {"type": "string"},
            "clock": {
                "anyOf": [_ref("ClockState"), {"type": "null"}],
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
            "positions": {"type": "array", "items": _ref("Position")},
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
        {"player": {"type": "string"}, "exercises": {"type": "array", "items": _ref("Exercise")}},
        ["player", "exercises"],
    ),
    "ExerciseStartResponse": _obj(
        {"game_id": {"type": "string"}, "exercise": _ref("Exercise")}, ["game_id", "exercise"]
    ),
    "ExerciseAnswerResponse": _obj(
        {"status": {"type": "string", "enum": ["ok"]}, "exercise": _ref("Exercise")},
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
                "additionalProperties": _ref("MotifCounts"),
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
            "clock": {"anyOf": [_ref("ClockState"), {"type": "null"}]},
        },
        ["status", "game_id", "color", "state", "clock"],
    ),
}

# --------------------------------------------------------------------------
# Per-route annotations
# --------------------------------------------------------------------------

TOKEN_ROUTES = {
    ("post", "/api/games"),
    ("post", "/api/games/{game_id}/move"),
    ("post", "/api/games/{game_id}/resign"),
    ("get", "/api/games/{game_id}/seat"),
    ("post", "/api/games/{game_id}/board"),
    ("post", "/api/games/{game_id}/synced"),
    ("get", "/api/me"),
    ("get", "/api/coach/exercises"),
    ("post", "/api/coach/exercises/{exercise_id}/start"),
    ("post", "/api/coach/exercises/{exercise_id}/answer"),
    ("get", "/api/coach/progress"),
    ("get", "/api/coach/reports/{game_id}"),
}

#: (summary, description, tag)
DOCS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("get", "/"): (
        "Page du viewer",
        "Page HTML de l'échiquier, avec sa Content-Security-Policy.",
        "viewer",
    ),
    ("get", "/api/health"): ("Santé", 'Répond `{"status": "ok"}`.', "viewer"),
    ("get", "/api/games"): (
        "Liste des parties",
        "Toutes les parties en cours, puis une page des parties finies, triées par date de "
        "modification. `q` cherche dans l'identifiant et les joueurs, `player` filtre sur un "
        "joueur (« mes parties »), `exercises=false` masque les parties non classées. 304 si le "
        "dossier n'a pas changé (ETag).",
        "parties",
    ),
    ("post", "/api/games"): (
        "Ouvrir une partie",
        "Partie non classée ouverte par le joueur du jeton, contre un agent ou un bot (`bot-*`, "
        "pour un créateur humain seulement). 5 parties en cours au plus par créateur (429 au-delà).",
        "parties",
    ),
    ("get", "/api/raw"): ("PGN brut", "Le PGN complet de la partie.", "parties"),
    ("post", "/api/games/{game_id}/move"): (
        "Jouer un coup",
        "Coup en `san` ou en `uci` (`e7e8q` pour une promotion). Le joueur du jeton doit avoir le "
        "trait. `by` sert seulement à l'audit.",
        "parties",
    ),
    ("post", "/api/games/{game_id}/resign"): (
        "Abandonner",
        "Abandon du camp du joueur du jeton. 409 si la partie est déjà finie.",
        "parties",
    ),
    ("get", "/api/games/{game_id}/seat"): (
        "Place du jeton",
        "Le joueur du jeton et son ou ses camps dans la partie, sans rien jouer. Utilisé par le "
        "Tablier au lancement.",
        "parties",
    ),
    ("post", "/api/games/{game_id}/board"): (
        "Déclarer un échiquier physique",
        "Le pont du Tablier joue sur un eBoard (`board: true`), ou plus (`false`). Réservé à un "
        "joueur humain (#164).",
        "Tablier",
    ),
    ("post", "/api/games/{game_id}/synced"): (
        "Échiquier synchronisé",
        "L'eBoard montre ce placement (`fen`). La pendule du joueur repart quand l'échiquier "
        "correspond à la position du serveur (#164).",
        "Tablier",
    ),
    ("get", "/api/piece.svg"): ("Pièce en SVG", "Une pièce seule, cache immuable.", "rendu"),
    ("get", "/api/board.svg"): (
        "Échiquier en SVG",
        "L'échiquier d'une partie au demi-coup `ply` (0 : position de départ de la partie).",
        "rendu",
    ),
    ("get", "/api/positions"): (
        "Positions de la partie",
        "Toutes les positions de la partie, une par demi-coup. GET conditionnel : ETag et 304.",
        "parties",
    ),
    ("get", "/api/clock"): (
        "Pendule",
        "État de la pendule. Lue avec le jeton d'un joueur ou de l'arbitre, elle termine aussi "
        "une partie au temps (#135).",
        "parties",
    ),
    ("get", "/api/engine/live"): (
        "Analyse moteur",
        "Évaluation de la position courante (ou de `fen`) à la profondeur `depth`. Le garde-fou "
        "moteur refuse l'analyse d'une partie en cours selon le rôle `viewer`.",
        "analyse",
    ),
    ("get", "/api/games/{game_id}/stream"): (
        "Flux temps réel (SSE)",
        "Server-Sent Events : un instantané JSON sur une ligne `data:` à chaque changement du "
        "PGN, un commentaire SSE toutes les 15 s, et un événement d'erreur `GAME_NOT_FOUND` "
        "si la partie disparaît.",
        "parties",
    ),
    ("get", "/api/commentary"): (
        "Lire les commentaires",
        "Commentaires de la partie, éventuellement d'un demi-coup.",
        "analyse",
    ),
    ("post", "/api/commentary"): (
        "Ajouter un commentaire",
        "Commentaire sur un demi-coup (`ply`, `source`, `text`). Sans jeton à ce jour.",
        "analyse",
    ),
    ("get", "/api/elo"): ("Classement Elo", "Le classement Elo courant.", "joueurs"),
    ("get", "/api/me"): (
        "Qui suis-je",
        "`{player, kind}` du jeton, sans partie. Utilisé par le Tablier pour se lancer sans `--game`.",
        "joueurs",
    ),
    ("get", "/api/coach/exercises"): (
        "Mes exercices",
        "Les exercices du joueur du jeton, sans leur solution tant qu'ils ne sont pas résolus.",
        "coach",
    ),
    ("post", "/api/coach/exercises/{exercise_id}/start"): (
        "Commencer un exercice",
        "Ouvre une partie sur la position de l'exercice, pour le joueur du jeton.",
        "coach",
    ),
    ("post", "/api/coach/exercises/{exercise_id}/answer"): (
        "Répondre à un exercice",
        "Réponse en cases (`squares`, 16 au plus), pour un exercice de type `attacked`.",
        "coach",
    ),
    ("get", "/api/coach/progress"): (
        "Ma progression",
        "Compteurs par motif, suggestion, file d'attente et rappel du joueur du jeton.",
        "coach",
    ),
    ("get", "/api/coach/reports/{game_id}"): (
        "Rapport de partie",
        "Le rapport du coach. Réservé aux joueurs et à l'arbitre de la partie.",
        "coach",
    ),
}

#: Success response schema of the JSON routes.
OK_SCHEMAS = {
    ("get", "/api/games"): "GamesList",
    ("post", "/api/games"): "CreatedGame",
    ("get", "/api/clock"): "ClockResponse",
    ("get", "/api/positions"): "PositionsResponse",
    ("get", "/api/coach/exercises"): "ExercisesResponse",
    ("post", "/api/coach/exercises/{exercise_id}/start"): "ExerciseStartResponse",
    ("post", "/api/coach/exercises/{exercise_id}/answer"): "ExerciseAnswerResponse",
    ("get", "/api/coach/progress"): "Progress",
    ("get", "/api/me"): "Me",
    ("get", "/api/games/{game_id}/seat"): "Seat",
}

#: Routes whose success is not JSON.
MEDIA_TYPES = {
    ("get", "/"): ("text/html", {"type": "string"}),
    ("get", "/api/piece.svg"): ("image/svg+xml", {"type": "string"}),
    ("get", "/api/board.svg"): ("image/svg+xml", {"type": "string"}),
    ("get", "/api/games/{game_id}/stream"): ("text/event-stream", {"type": "string"}),
}

ERRORS = {
    "400": "Requête invalide (identifiant, coup ou paramètre incohérent)",
    "401": "Jeton absent, révoqué ou inconnu",
    "403": "Jeton valide, mais pas autorisé pour cette action",
    "404": "Partie ou ressource introuvable",
    "409": "Conflit avec l'état de la partie ou de l'exercice",
    "422": "Corps ou paramètre refusé",
    "429": "Trop de parties en cours pour ce créateur",
    "503": "Registre, données ou moteur indisponibles côté serveur",
}

#: Error codes read in the handlers, on top of 401/403/503 for the token routes.
EXTRA_ERRORS = {
    ("post", "/api/games"): ["422", "429"],
    ("post", "/api/games/{game_id}/move"): ["400", "404", "409", "422"],
    ("post", "/api/games/{game_id}/resign"): ["404", "409"],
    ("get", "/api/games/{game_id}/seat"): ["404"],
    ("post", "/api/games/{game_id}/board"): ["404"],
    ("post", "/api/games/{game_id}/synced"): ["404"],
    ("get", "/api/raw"): ["404"],
    ("get", "/api/board.svg"): ["404"],
    ("get", "/api/positions"): ["400", "404"],
    ("get", "/api/clock"): ["400"],
    ("get", "/api/engine/live"): ["400", "403", "404", "503"],
    ("get", "/api/coach/exercises"): ["422"],
    ("post", "/api/coach/exercises/{exercise_id}/start"): ["404", "409"],
    ("post", "/api/coach/exercises/{exercise_id}/answer"): ["404", "409", "422"],
    ("get", "/api/coach/reports/{game_id}"): ["400", "404"],
}

#: Token routes that never answer 403.
NO_403 = {
    ("get", "/api/me"),
    ("get", "/api/coach/exercises"),
    ("get", "/api/coach/progress"),
    ("post", "/api/coach/exercises/{exercise_id}/start"),
    ("post", "/api/coach/exercises/{exercise_id}/answer"),
}

CONDITIONAL = {("get", "/api/games"), ("get", "/api/positions")}


def _success(op: dict) -> dict:
    code = next((c for c in op["responses"] if c.startswith("2")), "200")
    return op["responses"][code]


def _annotate(method: str, path: str, op: dict) -> None:
    key = (method, path)
    op["summary"], op["description"], tag = DOCS[key]
    op["tags"] = [tag]
    ok = _success(op)
    if key in OK_SCHEMAS:
        ok["content"] = {"application/json": {"schema": _ref(OK_SCHEMAS[key])}}
    if key in MEDIA_TYPES:
        media, schema = MEDIA_TYPES[key]
        ok["content"] = {media: {"schema": schema}}
    if key in CONDITIONAL:
        ok["headers"] = {
            "ETag": {"schema": {"type": "string"}, "description": "à renvoyer en If-None-Match"}
        }
        op.setdefault("parameters", []).append(
            {
                "name": "If-None-Match",
                "in": "header",
                "required": False,
                "schema": {"type": "string"},
            }
        )
        op["responses"]["304"] = {
            "description": "Inchangé depuis l'ETag envoyé, sans corps",
            "headers": {"ETag": {"schema": {"type": "string"}}},
        }
    for param in op.get("parameters", []):
        if param["name"] == "viewer":
            param["schema"] = {"type": "string", "enum": list(VIEWER_ROLES), "default": "spectator"}
    codes = list(EXTRA_ERRORS.get(key, []))
    if key in TOKEN_ROUTES:
        op["security"] = [{"jetonJoueur": []}]
        codes += ["401", "503"] if key in NO_403 else ["401", "403", "503"]
    error = {"application/json": {"schema": _ref("Error")}}
    for code in sorted(set(codes)):
        op["responses"].setdefault(code, {"description": ERRORS[code], "content": error})


def build_spec() -> dict:
    """The annotated OpenAPI document ; KeyError for a route without annotation."""
    spec = build_app().openapi()
    spec["info"] = {
        "title": "API palamède (viewer)",
        "version": __version__,
        "description": DESCRIPTION,
    }
    spec["servers"] = [{"url": SERVER, "description": "Production"}]
    components = spec.setdefault("components", {})
    components["securitySchemes"] = {
        "jetonJoueur": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "plm_…",
            "description": "Jeton d'un joueur du registre palamède. Jamais dans l'URL.",
        }
    }
    schemas = components.setdefault("schemas", {})
    schemas.update(SCHEMAS)
    # rated is checked by hand in new_game (a strict bool) : the model says object.
    schemas["NewGamePayload"]["properties"]["rated"] = {"type": "boolean", "default": False}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            _annotate(method, path, op)
    spec["tags"] = [{"name": t} for t in TAGS]
    return spec


def render(spec: dict) -> str:
    return json.dumps(spec, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    DOC_PATH.write_text(render(build_spec()), encoding="utf-8")
    print(f"{DOC_PATH} écrit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
