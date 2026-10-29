"""Game creation from a player's token : ``POST /api/games`` (#93, PR 1).

Lets the Tablier app open a game against a chosen opponent (human,
agent or ``bot-*``) without the MCP tool ``chess_create_game``.

Rules (owner and Leader, #93, 28-29/10) :

* the ``Authorization: Bearer`` token identifies the creator, who always
  holds one of the two seats : a token never opens a game between two
  other players ;
* the opponent is an existing account of the registry, other than the
  creator ;
* **only unrated games** : ``rated`` is a strict JSON boolean, ``false``
  by default, and ``true`` is refused (400). A rated game keeps the
  arbiter-or-daemon contract of ``chess_create_game``, so this route
  opens no path around it, and a game against a bot never counts for
  the agents' Elo nor makes anyone busy for matchmaking (#33) ;
* no arbiter : the creator's token is enough for an unrated game ;
* the game id is chosen by the server, never by the client.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel

from palamede_chessboard.chessboard_clock import init_and_save_clock
from palamede_chessboard.chessboard_state import (
    GameAlreadyExistsError,
    GameMetadata,
    GameState,
    TimeControl,
)
from palamede_chessboard.players import registry
from palamede_chessboard.players.observe import bearer_token

log = logging.getLogger(__name__)

#: Event header of the games opened this way.
EVENT = "Tablier"
#: Attempts at a free game id before giving up (a clash needs the same
#: second and the same 16 random bits : one retry is already paranoid).
_ID_ATTEMPTS = 3


class TimeControlPayload(BaseModel):
    format: str = "unrated"
    start_seconds: int | None = None
    increment_seconds: int | None = None


class NewGamePayload(BaseModel):
    """Body of ``POST /api/games``. ``rated`` is checked by hand : strict bool."""

    opponent: str
    color: Literal["white", "black", "random"] = "random"
    rated: object = False
    time_control: TimeControlPayload | None = None


def _creator(request: Request, games_dir: Path) -> str:
    """Player of the bearer token ; 401 or 503 otherwise."""
    token = bearer_token(request.headers)
    try:
        who = registry.verify_token(games_dir, token) if token else None
    except registry.RegistryError as exc:
        log.warning("POST /api/games : registry unreadable (%s)", exc)
        raise HTTPException(503, "Registre des joueurs illisible : partie refusée") from exc
    if who is None:
        raise HTTPException(
            401,
            "Jeton de joueur requis ou invalide (UNAUTHENTICATED)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return who


def _opponent(raw: str, creator: str, games_dir: Path) -> str:
    """Normalised id of an existing account other than the creator ; 422 otherwise."""
    try:
        opponent = registry.player_id_of(raw)
        known = registry.load(games_dir)["players"]
    except registry.RegistryError as exc:
        raise HTTPException(422, f"Adversaire invalide : {raw!r}") from exc
    if opponent == creator:
        raise HTTPException(422, "Vous ne pouvez pas être votre propre adversaire")
    if opponent not in known:
        raise HTTPException(422, f"Adversaire inconnu : {opponent}")
    return opponent


def _new_game_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"tablier-{stamp}-{secrets.token_hex(2)}"


def create_from_viewer(payload: NewGamePayload, request: Request, games_dir: Path) -> dict:
    """Create an unrated game between the token's player and ``payload.opponent``."""
    if not isinstance(payload.rated, bool):
        raise HTTPException(400, f"rated doit être un booléen JSON, reçu {payload.rated!r}")
    if payload.rated:
        raise HTTPException(
            400, "Une partie classée exige un arbitre : elle se crée par chess_create_game"
        )
    creator = _creator(request, games_dir)
    opponent = _opponent(payload.opponent, creator, games_dir)

    color = payload.color
    if color == "random":
        color = secrets.choice(("white", "black"))
    white, black = (creator, opponent) if color == "white" else (opponent, creator)

    tc_raw = payload.time_control or TimeControlPayload()
    tc = TimeControl(
        format=tc_raw.format,
        start_seconds=tc_raw.start_seconds,
        increment_seconds=tc_raw.increment_seconds,
    )
    for _ in range(_ID_ATTEMPTS):
        metadata = GameMetadata(
            game_id=_new_game_id(),
            event=EVENT,
            white=white,
            black=black,
            time_control=tc,
            rated=False,
        )
        try:
            state = GameState.create(metadata, games_dir=games_dir)
            break
        except GameAlreadyExistsError:
            continue
    else:
        raise HTTPException(503, "Aucun identifiant de partie libre, réessayez")

    clock = init_and_save_clock(state.metadata.game_id, tc, games_dir=games_dir)
    log.info("viewer : %s created %s (%s vs %s)", creator, metadata.game_id, white, black)
    return {
        "status": "ok",
        "game_id": state.metadata.game_id,
        "color": color,
        "state": state.to_state_dict(),
        "clock": clock.to_dict() if clock is not None else None,
    }
