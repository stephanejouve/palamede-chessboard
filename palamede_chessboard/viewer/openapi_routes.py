"""Per-route annotations of the viewer's API (#264) : token, summaries,
success schemas, media types and error codes read in the handlers.

Assembled by :mod:`palamede_chessboard.viewer.openapi_doc`.
"""

from __future__ import annotations

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

TOKEN_ROUTES = {
    ("post", "/api/commentary"),
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
        "moteur (#206) refuse avec 403 une partie en cours, **quel que soit** `viewer` : le rôle "
        "est déclaré par le client et seulement comparé à la liste des rôles (400 sinon), et toute position par laquelle est passée une partie en "
        "cours, symétrique compris. `viewer` sert seulement au journal du moteur.",
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
        "Commentaire sur un demi-coup (`ply`, `source`, `text`). Jeton d'un joueur, de "
        "l'arbitre ou d'un spectateur déclaré de la partie.",
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
    ("post", "/api/games"): ["400", "422", "429"],
    ("post", "/api/games/{game_id}/move"): ["400", "404", "409", "422"],
    ("post", "/api/games/{game_id}/resign"): ["404", "409"],
    ("get", "/api/games/{game_id}/seat"): ["404"],
    ("post", "/api/games/{game_id}/board"): ["404"],
    ("post", "/api/games/{game_id}/synced"): ["404"],
    ("get", "/api/raw"): ["404"],
    ("get", "/api/piece.svg"): ["400"],
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
