"""Game creation from a player's token : ``POST /api/games`` (#93, PR 1).

Lets the Tablier app open a game against a chosen opponent (human,
agent or ``bot-*``) without the MCP tool ``chess_create_game``.

Rules (owner and Leader, #93, 28-29/10) :

* the ``Authorization: Bearer`` token identifies the creator, who always
  holds one of the two seats : a token never opens a game between two
  other players ;
* the opponent is an existing account of the registry, other than the
  creator, and **not a human** : a human is never put in a game without
  their consent ; games between humans stay created by hand until an
  invitation exists (owner, 29/10) ;
* at most :data:`MAX_OPEN_PER_CREATOR` games of this route in progress per
  creator, 429 beyond (owner, 29/10). The id names its creator,
  ``tablier.<creator>.<UTC stamp>-<hex>`` (a dot never appears in a player
  id), so an agent is never blocked by games others opened against it
  (review Junior #126). The count and the creation share one file lock,
  parallel requests included ;
* **only unrated games** : ``rated`` is a strict JSON boolean, ``false``
  by default, and ``true`` is refused (400). A rated game keeps the
  arbiter-or-daemon contract of ``chess_create_game``, so this route
  opens no path around it, and a game against a bot never counts for
  the agents' Elo nor makes anyone busy for matchmaking (#33) ;
* no arbiter : the creator's token is enough for an unrated game ;
* the game id is chosen by the server, never by the client ;
* the time control is ``unrated`` or ``M+S`` with bounded numbers, nothing
  else : the PGN writer does not escape header values, so a free-form
  format could forge headers (``Rated``, ``Result``…) and get round the
  unrated rule (review Leader and Junior, #126). No other field.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict

from palamede_chessboard import _atomic
from palamede_chessboard.chessboard_clock import init_and_save_clock
from palamede_chessboard.chessboard_state import (
    GameAlreadyExistsError,
    GameMetadata,
    GameState,
    TimeControl,
    list_games,
)
from palamede_chessboard.players import registry
from palamede_chessboard.players.observe import bearer_token

log = logging.getLogger(__name__)

#: Event header of the games opened this way.
EVENT = "Tablier"
#: Attempts at a free game id before giving up (a clash needs the same
#: second and the same 16 random bits : one retry is already paranoid).
_ID_ATTEMPTS = 3
#: ``M+S`` : minutes then increment seconds, digits only, no spaces.
_CLOCK_FORMAT = re.compile(r"([0-9]{1,3})\+([0-9]{1,3})")
#: Bounds of a clock : 1 to 180 minutes, 0 to 180 seconds of increment.
MAX_MINUTES = 180
MAX_INCREMENT = 180
#: Games opened by this route and still in progress, per creator (owner, 29/10).
MAX_OPEN_PER_CREATOR = 5
#: First part of the ids this route gives, before the creator's name.
ID_PREFIX = "tablier"
#: Lock taken around the count and the creation (``<games dir>/.tablier-create.lock``).
LOCK_NAME = ".tablier-create"


class TimeControlPayload(BaseModel):
    """Only ``format`` : ``start_seconds`` and the like are refused (422)."""

    model_config = ConfigDict(extra="forbid")

    format: str = "unrated"


def _time_control(raw: TimeControlPayload | None) -> TimeControl:
    """``unrated`` or a bounded ``M+S`` ; 422 for anything else."""
    fmt = raw.format if raw is not None else "unrated"
    if fmt == "unrated":
        return TimeControl(format=fmt)
    match = _CLOCK_FORMAT.fullmatch(fmt)
    if match is None:
        raise HTTPException(422, "Cadence invalide : « unrated » ou « M+S » (ex. 15+10)")
    minutes, increment = (int(g) for g in match.groups())
    if not 1 <= minutes <= MAX_MINUTES or increment > MAX_INCREMENT:
        raise HTTPException(
            422, f"Cadence hors bornes : 1 à {MAX_MINUTES} min, 0 à {MAX_INCREMENT} s d'incrément"
        )
    return TimeControl(format=f"{minutes}+{increment}")


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
    # Fail-closed (review Leader #126) : only an agent account, bots included.
    if known[opponent].get("kind") != "agent":
        raise HTTPException(
            422, "Pas encore de partie contre un humain par Tablier : elle se crée à la main"
        )
    return opponent


def _check_open_games(creator: str, games_dir: Path) -> None:
    """429 when the creator already has :data:`MAX_OPEN_PER_CREATOR` games of this route."""
    mine = f"{ID_PREFIX}.{creator}."
    open_games = [
        g for g in list_games(games_dir, active_only=True) if g["game_id"].startswith(mine)
    ]
    if len(open_games) >= MAX_OPEN_PER_CREATOR:
        raise HTTPException(
            429,
            f"Déjà {len(open_games)} parties Tablier en cours : terminez-en une d'abord",
        )


def _new_game_id(creator: str) -> str:
    """``tablier.<creator>.<stamp>-<hex>`` : 61 characters at most, under the 64 allowed."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{ID_PREFIX}.{creator}.{stamp}-{secrets.token_hex(2)}"


def _create_game(
    creator: str, white: str, black: str, tc: TimeControl, games_dir: Path
) -> GameState:
    """A new unrated game under a fresh id ; 503 if none is free."""
    for _ in range(_ID_ATTEMPTS):
        metadata = GameMetadata(
            game_id=_new_game_id(creator),
            event=EVENT,
            white=white,
            black=black,
            time_control=tc,
            rated=False,
        )
        try:
            return GameState.create(metadata, games_dir=games_dir)
        except GameAlreadyExistsError:
            continue
    raise HTTPException(503, "Aucun identifiant de partie libre, réessayez")


def create_from_viewer(payload: NewGamePayload, request: Request, games_dir: Path) -> dict:
    """Create an unrated game between the token's player and ``payload.opponent``."""
    creator = _creator(request, games_dir)
    if not isinstance(payload.rated, bool):
        raise HTTPException(400, f"rated doit être un booléen JSON, reçu {payload.rated!r}")
    if payload.rated:
        raise HTTPException(
            400, "Une partie classée exige un arbitre : elle se crée par chess_create_game"
        )
    tc = _time_control(payload.time_control)
    opponent = _opponent(payload.opponent, creator, games_dir)

    color = payload.color
    if color == "random":
        color = secrets.choice(("white", "black"))
    white, black = (creator, opponent) if color == "white" else (opponent, creator)

    # Count then create under one lock : FastAPI runs ``def`` routes in a
    # thread pool, and 12 parallel POSTs made up to 12 games without it
    # (review Leader #126). A file lock, so it holds across workers too.
    with _atomic.locked(games_dir / LOCK_NAME):
        _check_open_games(creator, games_dir)
        state = _create_game(creator, white, black, tc, games_dir)

    clock = init_and_save_clock(state.metadata.game_id, tc, games_dir=games_dir)
    log.info("viewer : %s created %s (%s vs %s)", creator, state.metadata.game_id, white, black)
    return {
        "status": "ok",
        "game_id": state.metadata.game_id,
        "color": color,
        "state": state.to_state_dict(),
        "clock": clock.to_dict() if clock is not None else None,
    }
