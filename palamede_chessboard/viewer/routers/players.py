"""Routes for the ``players`` domain : who a token is (#90).

Endpoint covered :

* ``GET /api/me`` — the token's player and kind, no game needed. Lets the
  Tablier app find its account at launch, then the player's games (#90),
  and the « Nouvelle partie » screen know its creator (#93).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response

from palamede_chessboard.players import registry
from palamede_chessboard.players.observe import bearer_token
from palamede_chessboard.viewer.models import _games_dir

log = logging.getLogger(__name__)

UNAUTHENTICATED = "Jeton de joueur requis ou invalide (bouton 🔑)"
REGISTRY_UNREADABLE = "Registre des joueurs illisible"


def build_players_router() -> APIRouter:
    """Return the ``players`` ``APIRouter`` ready to mount on ``build_app``."""
    router = APIRouter(tags=["players"])

    @router.get("/api/me")
    def me(request: Request, response: Response) -> dict:
        """The token's player ; see :func:`player_of_token`."""
        response.headers["Cache-Control"] = "no-store"
        return player_of_token(request)

    return router


def player_of_token(request: Request) -> dict:
    """``{player, kind}`` of the Bearer token ; 401 if it is missing or unknown.

    Same answer whatever the reason a token is refused (absent, malformed,
    revoked, unknown), like ``/seat`` : nothing tells a revoked token from a
    made-up one. 503 when the registry cannot be read, never a pass. It
    tells the caller only their own account.
    """
    gdir = _games_dir()
    token = bearer_token(request.headers)
    try:
        who = registry.verify_token(gdir, token) if token else None
        player = registry.load(gdir)["players"].get(who) if who else None
    except registry.RegistryError as exc:
        log.warning("/api/me : registry unreadable (%s)", exc)
        raise HTTPException(503, REGISTRY_UNREADABLE) from exc
    if who is None or player is None:
        raise HTTPException(401, UNAUTHENTICATED, headers={"WWW-Authenticate": "Bearer"})
    return {"player": who, "kind": player.get("kind")}
