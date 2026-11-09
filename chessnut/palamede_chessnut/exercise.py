"""The exercise's banner under the board (#237, step 2).

An ``exo.*`` game is an exercise of the coach : its family and how to answer
it are said under the board, with the viewer's words (#237, step 1). The
bridge reads the family once it runs (:func:`read`) ; « Cases attaquées » is
answered in the viewer, the Tablier only sends moves. An older viewer, or a
game that is no exercise, shows nothing. A line says why the position was
chosen : the mistake of the game it comes from (Stéphane, 05/11).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from palamede_chessnut import viewer

if TYPE_CHECKING:
    from palamede_chessnut.bridge import Bridge

log = logging.getLogger(__name__)

EXERCISE_PREFIX = "exo."

#: The family's title and how to answer it on the Tablier, as in the viewer.
BANNERS: dict[str, tuple[str, str]] = {
    "find_move": (
        "Trouvez le bon coup",
        "Trouvez le meilleur coup dans cette position, dans l'app Tablier "
        "ou à la souris dans la visionneuse. "
        "Un coup faux est refusé et la position reste en place : réessayez.",
    ),
    "attacked": (
        "Cases attaquées",
        "Levez puis reposez chacune de vos pièces en prise : sa case s'allume, "
        "le même geste l'enlève. Validez en levant puis reposant votre roi deux fois, "
        "même s'il n'y a aucune pièce en prise. La visionneuse accepte aussi la réponse.",
    ),
}
#: Without a board, nothing designates squares : the mouse only plays moves (#92).
ATTACKED_WITHOUT_BOARD = (
    "Cet exercice se fait dans la visionneuse : la souris ne joue ici que des coups."
)


#: The motif's name and why the position was chosen, as in the viewer.
MOTIFS: dict[str, tuple[str, str]] = {
    "missed_mate": (
        "Mat manqué",
        "Dans votre partie, un mat forcé était possible ici, et il a été manqué.",
    ),
    "hanging_piece": (
        "Pièce laissée en prise",
        "Dans votre partie, le coup joué ici a laissé une de vos pièces en prise.",
    ),
    "ignored_threat": (
        "Menace ignorée",
        "Dans votre partie, une de vos pièces était déjà en prise ici, "
        "et le coup joué ne l'a pas sauvée.",
    ),
}


def found(exercises: Any, game_id: str) -> dict[str, str] | None:
    """``{"id", "kind", "motif"}`` of the exercise played in ``game_id``, or ``None``."""
    if not isinstance(exercises, dict) or not isinstance(exercises.get("exercises"), list):
        return None
    for exercise in exercises["exercises"]:
        if isinstance(exercise, dict) and game_id in (exercise.get("game_ids") or []):
            kind, motif, exercise_id = (
                exercise.get("kind"),
                exercise.get("motif"),
                exercise.get("id"),
            )
            if not isinstance(kind, str):
                return None
            return {
                "id": exercise_id if isinstance(exercise_id, str) else "",
                "kind": kind,
                "motif": motif if isinstance(motif, str) else "",
            }
    return None


def read(bridge: Bridge) -> dict[str, str] | None:
    """Ask the viewer the bridge's exercise ; kept on ``bridge.exercise``."""
    if not bridge.game_id.lower().startswith(EXERCISE_PREFIX):
        return None
    status, body = viewer.get_with_token(f"{bridge.viewer}/api/coach/exercises", bridge._token)
    if status != 200:
        log.info("exercise family unknown (HTTP %s)", status)
        return None
    bridge.exercise = found(body, bridge.game_id)
    return bridge.exercise


def answer(bridge: Bridge, squares: list[str]) -> tuple[int | None, Any]:
    """Post the squares designated on the board : ``(status, JSON body or None)``."""
    assert bridge.exercise is not None
    exercise_id = quote(bridge.exercise["id"], safe="")
    url = f"{bridge.viewer}/api/coach/exercises/{exercise_id}/answer"
    return viewer.post_json(url, bridge._token, {"squares": squares})


def banner(bridge: Bridge) -> dict[str, str] | None:
    """``{"title", "context", "text"}`` for the page, or ``None`` : no exercise, or an unknown family."""
    if bridge.exercise is None or bridge.exercise["kind"] not in BANNERS:
        return None
    title, text = BANNERS[bridge.exercise["kind"]]
    if bridge.exercise["kind"] == "attacked" and not bridge.with_board:
        text = ATTACKED_WITHOUT_BOARD
    motif = MOTIFS.get(bridge.exercise["motif"])
    if motif is None:
        return {"title": title, "context": "", "text": text}
    return {"title": f"{motif[0]} · {title}", "context": motif[1], "text": text}
