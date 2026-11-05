"""The exercise's banner under the board (#237, step 2).

An ``exo.*`` game is an exercise of the coach : its family and how to answer
it are said under the board, with the viewer's words (#237, step 1). The
bridge reads the family once it runs (:func:`read`) ; « Cases attaquées » is
answered in the viewer, the Tablier only sends moves. An older viewer, or a
game that is no exercise, shows nothing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from palamede_chessnut import viewer

if TYPE_CHECKING:
    from palamede_chessnut.bridge import Bridge

log = logging.getLogger(__name__)

EXERCISE_PREFIX = "exo."

#: The family's title and how to answer it on the Tablier, as in the viewer.
BANNERS: dict[str, tuple[str, str]] = {
    "find_move": (
        "Trouvez le bon coup",
        "Trouvez le meilleur coup dans cette position, au Tablier ou à la souris. "
        "Un coup faux est refusé et la position reste en place : réessayez.",
    ),
    "attacked": (
        "Cases attaquées",
        "Cet exercice se fait dans la visionneuse : le Tablier n'envoie que des coups.",
    ),
}


def kind_of(exercises: Any, game_id: str) -> str | None:
    """The family of the exercise played in ``game_id``, or ``None``."""
    if not isinstance(exercises, dict) or not isinstance(exercises.get("exercises"), list):
        return None
    for exercise in exercises["exercises"]:
        if isinstance(exercise, dict) and game_id in (exercise.get("game_ids") or []):
            kind = exercise.get("kind")
            return kind if isinstance(kind, str) else None
    return None


def read(bridge: Bridge) -> str | None:
    """Ask the viewer the family of the bridge's exercise ; kept on ``bridge.exercise_kind``."""
    if not bridge.game_id.lower().startswith(EXERCISE_PREFIX):
        return None
    status, body = viewer.get_with_token(f"{bridge.viewer}/api/coach/exercises", bridge._token)
    if status != 200:
        log.info("exercise family unknown (HTTP %s)", status)
        return None
    bridge.exercise_kind = kind_of(body, bridge.game_id)
    return bridge.exercise_kind


def banner(bridge: Bridge) -> dict[str, str] | None:
    """``{"title", "text"}`` for the page, or ``None`` : no exercise, or an unknown family."""
    found = BANNERS.get(bridge.exercise_kind or "")
    if found is None:
        return None
    title, text = found
    return {"title": title, "text": text}
