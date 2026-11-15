"""Routes for the ``players`` domain : who a token is (#90), and its tokens (#351).

Endpoints covered :

* ``GET /api/me`` — the token's player and kind, no game needed. Lets the
  Tablier app find its account at launch, then the player's games (#90),
  and the « Nouvelle partie » screen know its creator (#93).
* ``GET /api/me/tokens`` — the token's own player's tokens : id, label,
  class, dates, revocation, and which one made the request. Never a
  secret nor a digest (#351 §6).
* ``DELETE /api/me/tokens/{token_id}`` — revokes one of **its own**
  tokens, with immediate effect ; another player's token is a 404, as an
  unknown one (#351 §6). Revoking the session's own token also expires
  the cookie.
* ``POST /api/session`` — the token pasted with 🔑, of the account's
  classes or ``recovery``, is exchanged for a new ``browser`` token in the
  session cookie (#351 §2, see :mod:`palamede_chessboard.viewer.session`).
  The pasted token is never kept by the page.
* ``POST /api/session/logout`` — expires the cookie. The token stays valid
  on the server : revoking is a separate act (owner, 14/11).

Both session routes answer only to this page (``Origin``), cookie or not :
another site cannot sign a browser in nor out.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from palamede_chessboard.players import registry
from palamede_chessboard.players.observe import bearer_token
from palamede_chessboard.viewer import session
from palamede_chessboard.viewer.models import _games_dir

log = logging.getLogger(__name__)

UNAUTHENTICATED = "Jeton de joueur requis ou invalide (bouton 🔑)"
REGISTRY_UNREADABLE = "Registre des joueurs illisible"
NO_SUCH_TOKEN = "Aucun jeton de ce numéro pour ce joueur"
TOO_MANY = "Trop d'essais de connexion : réessayez dans une minute"
#: The classes that may open a browser session : the account's own, and the
#: recovery token, whose only use it is (owner, 14/11).
SESSION_CLASSES = registry.ACCOUNT_CLASSES | {"recovery"}
#: 10 tries a minute per client address (design #351 §3) : no token guessed in series.
SESSION_TRIES = session.RateLimit(10, 60)


class SessionPayload(BaseModel):
    token: str = Field(max_length=128)


def build_players_router() -> APIRouter:
    """Return the ``players`` ``APIRouter`` ready to mount on ``build_app``."""
    router = APIRouter(tags=["players"])

    @router.get("/api/me")
    def me(request: Request, response: Response) -> dict:
        """The token's player ; see :func:`player_of_token`."""
        response.headers["Cache-Control"] = "no-store"
        return player_of_token(request)

    @router.get("/api/me/tokens")
    def my_tokens(request: Request, response: Response) -> dict:
        """The token's player's tokens, the one of this request marked ``current``."""
        response.headers["Cache-Control"] = "no-store"
        use = _token_use(request)
        try:
            listed = registry.tokens_of(_games_dir(), use.player_id)
        except registry.RegistryError as exc:
            log.warning("/api/me/tokens : registry unreadable (%s)", exc)
            raise HTTPException(503, REGISTRY_UNREADABLE) from exc
        for entry in listed:
            entry["current"] = entry["id"] == use.token_id
        return {"player": use.player_id, "tokens": listed}

    @router.delete("/api/me/tokens/{token_id}")
    def revoke_my_token(token_id: str, request: Request, response: Response) -> dict:
        """Revoke one of the player's own tokens ; 404 for any other."""
        use = _token_use(request)
        gdir = _games_dir()
        try:
            if not any(t["id"] == token_id for t in registry.tokens_of(gdir, use.player_id)):
                raise HTTPException(404, NO_SUCH_TOKEN)
            registry.revoke_token(gdir, use.player_id, token_id)
        except registry.RegistryError as exc:
            log.warning("DELETE /api/me/tokens/%s : registry unreadable (%s)", token_id, exc)
            raise HTTPException(503, REGISTRY_UNREADABLE) from exc
        if token_id == use.token_id and getattr(request.state, "session_cookie", False):
            session.clear_session_cookie(response)
        return {"player": use.player_id, "revoked": token_id}

    @router.post("/api/session")
    def open_session(payload: SessionPayload, request: Request, response: Response) -> dict:
        """A ``browser`` token in the cookie, for the player of the pasted token."""
        response.headers["Cache-Control"] = "no-store"
        _same_page(request)
        wait = SESSION_TRIES.retry_after(request.client.host if request.client else "")
        if wait is not None:
            raise HTTPException(429, TOO_MANY, headers={"Retry-After": str(wait)})
        gdir = _games_dir()
        try:
            use = registry.verify(gdir, payload.token, SESSION_CLASSES)
            if use is None:
                raise HTTPException(401, UNAUTHENTICATED)
            label = "navigateur " + datetime.now(UTC).strftime("%Y-%m-%d")
            browser = registry.issue_token(gdir, use.player_id, label=label, token_class="browser")
            kind = registry.load(gdir)["players"][use.player_id].get("kind")
        except registry.RegistryError as exc:
            log.warning("/api/session : registry unreadable (%s)", exc)
            raise HTTPException(503, REGISTRY_UNREADABLE) from exc
        session.set_session_cookie(response, browser)
        return {"player": use.player_id, "kind": kind}

    @router.post("/api/session/logout")
    def close_session(request: Request, response: Response) -> dict:
        """Expire the cookie ; the token stays valid, listed in « Mes jetons »."""
        _same_page(request)
        session.clear_session_cookie(response)
        return {"signed_out": True}

    return router


def _same_page(request: Request) -> None:
    """403 unless the request comes from the viewer's page (``Origin``)."""
    if not session.same_origin(request.scope["headers"]):
        raise HTTPException(403, session.CROSS_SITE)


def _token_use(request: Request) -> registry.TokenUse:
    """The request's token, of the account's classes ; 401 or 503 otherwise."""
    token = bearer_token(request.headers)
    try:
        use = registry.verify(_games_dir(), token, registry.ACCOUNT_CLASSES) if token else None
    except registry.RegistryError as exc:
        log.warning("registry unreadable (%s)", exc)
        raise HTTPException(503, REGISTRY_UNREADABLE) from exc
    if use is None:
        raise HTTPException(401, UNAUTHENTICATED, headers={"WWW-Authenticate": "Bearer"})
    return use


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
