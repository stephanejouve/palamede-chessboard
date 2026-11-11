"""Routes for lessons (``cours.*``, #323).

* ``POST /api/lessons`` — open a lesson (201) ; body ``{theme, white, black}``.

The same operation as the MCP tool ``chess_create_lesson`` : both call
:func:`palamede_chessboard.cours.create_lesson`, which holds the rules and
the refusals (owner, 11/11, #328). This route adds only who asks : the
token's player sits in the lesson, as white or black (403 otherwise).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from palamede_chessboard.cours import LessonError, create_lesson
from palamede_chessboard.players import registry
from palamede_chessboard.viewer.models import _games_dir
from palamede_chessboard.viewer.routers.players import player_of_token

log = logging.getLogger(__name__)

NOT_YOUR_LESSON = "Vous n'ouvrez un cours que si vous y jouez, aux blancs ou aux noirs"


class LessonPayload(BaseModel):
    """Body of ``POST /api/lessons`` ; any other field is refused (422)."""

    model_config = ConfigDict(extra="forbid")

    theme: str
    white: str
    black: str


def _seated(who: str, payload: LessonPayload) -> bool:
    """Whether the token's player is one of the two, compared on normalised ids."""
    for raw in (payload.white, payload.black):
        try:
            if registry.player_id_of(raw) == who:
                return True
        except registry.RegistryError:
            continue
    return False


def build_lessons_router() -> APIRouter:
    """Return the ``lessons`` ``APIRouter`` ready to mount on ``build_app``."""
    router = APIRouter(tags=["lessons"])

    @router.post("/api/lessons", status_code=201)
    def lesson_create(payload: LessonPayload, request: Request) -> dict:
        who = player_of_token(request)["player"]
        if not _seated(who, payload):
            raise HTTPException(403, NOT_YOUR_LESSON)
        try:
            state = create_lesson(
                _games_dir(), theme=payload.theme, white=payload.white, black=payload.black
            )
        except LessonError as exc:
            raise HTTPException(422, str(exc)) from exc
        log.info("viewer : %s opened the lesson %s", who, state.metadata.game_id)
        return {"status": "ok", "game_id": state.metadata.game_id, "state": state.to_state_dict()}

    return router
