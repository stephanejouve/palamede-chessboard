"""Routes for the ``coach`` domain : a player's exercises and reports (#95).

Endpoints covered, all under a player's token (401 without, 503 when the
registry cannot be read) :

* ``GET /api/coach/exercises`` — the token's own exercises, no solution
  before one is solved ; ``?state=`` filters.
* ``POST /api/coach/exercises/{id}/start`` — opens the exercise's game
  (201) ; an exercise still open is abandoned first.
* ``POST /api/coach/exercises/{id}/answer`` — « what is attacked ? »,
  answered with squares.
* ``GET /api/coach/reports/{game_id}`` — the report of a game, for its
  players and its arbiter only (403 otherwise).

The identity comes from the token only, never from a parameter, and is
compared on normalised ids (Admin, #95). No MCP tool yet : the MCP does
not authenticate.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from palamede_chessboard.chessboard_state import ChessboardError, GameNotFoundError, GameState
from palamede_chessboard.coach import play
from palamede_chessboard.coach.store import (
    EXERCISE_STATES,
    CoachStoreError,
    read_exercises,
    read_report,
)
from palamede_chessboard.players import registry
from palamede_chessboard.viewer.models import _games_dir, _not_found_detail
from palamede_chessboard.viewer.routers.players import player_of_token

log = logging.getLogger(__name__)

STORE_UNREADABLE = "Exercices illisibles : rien n'est changé"


class AnswerPayload(BaseModel):
    squares: list[str] = Field(max_length=16)


def build_coach_router() -> APIRouter:
    """Return the ``coach`` ``APIRouter`` ready to mount on ``build_app``."""
    router = APIRouter(tags=["coach"])

    @router.get("/api/coach/exercises")
    def exercises(request: Request, response: Response, state: str | None = None) -> dict:
        response.headers["Cache-Control"] = "no-store"
        who = player_of_token(request)["player"]
        if state is not None and state not in EXERCISE_STATES:
            raise HTTPException(422, f"state : l'un de {', '.join(EXERCISE_STATES)}")
        try:
            mine = read_exercises(_games_dir(), who)
        except CoachStoreError as exc:
            raise _store_error(exc) from exc
        if state is not None:
            mine = [e for e in mine if e.get("state") == state]
        return {"player": who, "exercises": [play.public_view(e) for e in mine]}

    @router.post("/api/coach/exercises/{exercise_id}/start", status_code=201)
    def start(exercise_id: str, request: Request) -> dict:
        who = player_of_token(request)["player"]
        return _run(lambda: play.start(_games_dir(), who, exercise_id))

    @router.post("/api/coach/exercises/{exercise_id}/answer")
    def answer(exercise_id: str, payload: AnswerPayload, request: Request) -> dict:
        who = player_of_token(request)["player"]
        exercise = _run(lambda: play.answer(_games_dir(), who, exercise_id, payload.squares))
        return {"status": "ok", "exercise": exercise}

    @router.get("/api/coach/reports/{game_id}")
    def report(game_id: str, request: Request, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return report_for_token(game_id, request)

    return router


def _store_error(exc: Exception) -> HTTPException:
    log.warning("coach : %s", exc)
    return HTTPException(503, STORE_UNREADABLE)


def _run(action):
    try:
        return action()
    except play.ExerciseError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except CoachStoreError as exc:
        raise _store_error(exc) from exc


def _ids(*names: str | None) -> set[str]:
    """Normalised ids of ``names`` ; a name no account carries gives none."""
    found = set()
    for name in names:
        try:
            found.add(registry.player_id_of(name))
        except registry.RegistryError:
            continue
    return found


def report_for_token(game_id: str, request: Request) -> dict:
    """The report of ``game_id`` if the token plays in it or arbitrates it."""
    who = player_of_token(request)["player"]
    gdir = _games_dir()
    try:
        state = GameState.load(game_id, games_dir=gdir)
    except GameNotFoundError as exc:
        raise HTTPException(404, _not_found_detail(game_id)) from exc
    except ChessboardError as exc:
        raise HTTPException(400, str(exc)) from exc
    meta = state.metadata
    if who not in _ids(meta.white, meta.black, meta.arbiter):
        raise HTTPException(403, "Rapport réservé aux joueurs et à l'arbitre de la partie")
    found = read_report(gdir, game_id)
    if found is None:
        raise HTTPException(404, "Pas encore de rapport pour cette partie")
    return found
