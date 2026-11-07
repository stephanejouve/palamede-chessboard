"""The coach's gestures in the lobby (#186, step 2) : two white pieces lifted for 5 s.

From the starting position, like the knights choosing the clock :

* both white bishops lifted (c1 + f1) : an exercise, the one the coach
  suggests next (``GET /api/coach/progress``, the head of its queue, #230),
  opened through ``POST /api/coach/exercises/{id}/start`` ; the window then
  moves to its ``exo.*`` game, as for a new game ;
* both white knights lifted (b1 + g1) : the lesson of the day, which waits
  for the coach's lessons (#95, step 3) : the window says « bientôt ».

The exact set of lifted squares is compared : neither pair overlaps the
rooks' corners, nor the king lifted to resign.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import chess

from palamede_chessnut import viewer

LESSON = "lesson"
EXERCISE = "exercise"
#: Gesture → its two squares, emptied, the rest of the starting position standing.
PAIRS: dict[str, tuple[chess.Square, chess.Square]] = {
    EXERCISE: (chess.C1, chess.F1),
    LESSON: (chess.B1, chess.G1),
}
SOON = "Les cours arrivent bientôt : en attendant, les fous lancent un exercice."
NONE_WAITING = "Aucun exercice en attente : jouez une partie, le coach en tirera."
NO_SERVER = "Le serveur n'a pas répondu : exercice non ouvert."
#: HTTP status → what the window says when the server's own words are missing.
REFUSALS = {
    401: "Jeton refusé par le serveur : exercice non ouvert.",
    404: "Exercice inconnu du serveur.",
    503: "Exercices illisibles sur le serveur : réessayez plus tard.",
}


def pair_gesture(observed: chess.BaseBoard) -> str | None:
    """:data:`LESSON` or :data:`EXERCISE` when exactly its pair is lifted, else ``None``."""
    for name, squares in PAIRS.items():
        start = chess.BaseBoard()
        for square in squares:
            start.remove_piece_at(square)
        if observed == start:
            return name
    return None


def _refusal(status: int | None, body: Any) -> str:
    if status is None:
        return NO_SERVER
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail:
        return f"Exercice non ouvert : {detail}"
    return REFUSALS.get(status, f"Exercice non ouvert (HTTP {status}).")


def start_exercise(viewer_url: str, token: str) -> tuple[str | None, chess.Color, str | None]:
    """``(game_id, side, None)`` once the suggested exercise is opened, else ``(None, _, refusal)``.

    ``side`` is the player's in the exercise : the board is guided from it.
    """
    status, body = viewer.get_with_token(f"{viewer_url}/api/coach/progress", token)
    if status != 200 or not isinstance(body, dict):
        return None, chess.WHITE, _refusal(status, body)
    suggested = body.get("suggestion")
    exercise_id = suggested.get("id") if isinstance(suggested, dict) else None
    if not isinstance(exercise_id, str):
        return None, chess.WHITE, NONE_WAITING
    status, body = viewer.post_json(
        f"{viewer_url}/api/coach/exercises/{quote(exercise_id, safe='')}/start", token, {}
    )
    game_id = body.get("game_id") if isinstance(body, dict) else None
    if status is None or not 200 <= status < 300 or not isinstance(game_id, str):
        return None, chess.WHITE, _refusal(status, body)
    exercise = body.get("exercise") if isinstance(body, dict) else None
    side = exercise.get("side") if isinstance(exercise, dict) else None
    return game_id, chess.BLACK if side == "black" else chess.WHITE, None
