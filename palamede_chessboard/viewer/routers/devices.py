"""Routes for pairing a board to a player (#351 §4, #227, RFC 8628).

Endpoints covered :

* ``POST /api/device/code`` — the board asks to be paired : a secret
  ``device_code``, a short ``user_code`` to show, ``/appairer``, the
  lifetime and the poll interval. No token needed : the board has none
  yet. 10 a minute per address.
* ``POST /api/device/token`` — the board polls with its ``device_code`` :
  400 ``authorization_pending``, ``slow_down``, ``access_denied`` or
  ``expired_token`` (RFC 8628 §3.5), then once the ``device`` token of
  the player who said yes. 60 polls a minute per address.
* ``POST /api/device/lookup`` — the signed-in player typed a code : the
  board's announced name and the hour of its request, to check before
  saying yes.
* ``POST /api/device/approve`` — the player's yes (``accept: true``) or
  no ; the code serves once.

The player's two routes take an ``api`` or ``browser`` token : a
``device`` token never pairs another board (a stolen board must not
breed more). Five wrong codes a minute per account, then 429. The
cookie's writes come from the page (``Origin``, session middleware).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from palamede_chessboard.players import pairing, registry
from palamede_chessboard.players.observe import bearer_token
from palamede_chessboard.viewer import session
from palamede_chessboard.viewer.models import _games_dir
from palamede_chessboard.viewer.routers.players import REGISTRY_UNREADABLE, UNAUTHENTICATED

log = logging.getLogger(__name__)

UNKNOWN_CODE = "Code inconnu, expiré ou déjà utilisé"
TOO_MANY_CODES = "Trop de codes faux : réessayez dans une minute"
TOO_MANY_REQUESTS = "Trop de demandes d'appairage : réessayez dans une minute"
BUSY = "Trop d'appairages en cours : réessayez plus tard"
#: The tokens that may pair a board : not ``device`` (review of the design, #351 §4).
PAIRING_CLASSES = frozenset({"api", "browser"})
#: The pairings under way, in the memory of the one worker (see :mod:`.pairing`).
PAIRINGS = pairing.Pairings()
#: Codes asked per address (a board asks once per pairing).
CODE_REQUESTS = session.RateLimit(10, 60)
#: Polls per address (review Admin #357) : a board polls every 5 s or
#: slower, 12 a minute ; 60 leave room for a few boards behind one address.
POLLS = session.RateLimit(60, 60)
TOO_MANY_POLLS = "Trop de relevés : ralentissez"
#: Wrong codes typed per account (design #351 §4).
WRONG_CODES = session.RateLimit(5, 60)


class CodeRequest(BaseModel):
    device_name: str = Field(default="", max_length=200)


class TokenRequest(BaseModel):
    device_code: str = Field(max_length=128)


class CodeTyped(BaseModel):
    user_code: str = Field(max_length=32)


class Answer(CodeTyped):
    accept: bool


def _rfc_error(error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=400, headers={"Cache-Control": "no-store"})


def build_devices_router() -> APIRouter:
    """Return the ``devices`` ``APIRouter`` ready to mount on ``build_app``."""
    router = APIRouter(tags=["players"])

    @router.post("/api/device/code")
    def device_code(payload: CodeRequest, request: Request, response: Response) -> dict:
        """A new pairing for the board ; its ``device_code`` in clear this once."""
        response.headers["Cache-Control"] = "no-store"
        wait = CODE_REQUESTS.retry_after(request.client.host if request.client else "")
        if wait is not None:
            raise HTTPException(429, TOO_MANY_REQUESTS, headers={"Retry-After": str(wait)})
        try:
            return PAIRINGS.start(payload.device_name)
        except pairing.PairingError as exc:
            raise HTTPException(503, BUSY) from exc

    router.post("/api/device/token", response_model=None)(device_token)

    @router.post("/api/device/lookup")
    def lookup(payload: CodeTyped, request: Request, response: Response) -> dict:
        """The board behind a code : its announced name and the hour it asked."""
        response.headers["Cache-Control"] = "no-store"
        use = _pairing_player(request)
        return _with_code(use, lambda: PAIRINGS.describe(payload.user_code))

    @router.post("/api/device/approve")
    def approve(payload: Answer, request: Request, response: Response) -> dict:
        """The player's yes or no to the board ; the code serves once."""
        response.headers["Cache-Control"] = "no-store"
        use = _pairing_player(request)
        return _with_code(
            use, lambda: PAIRINGS.answer(payload.user_code, use.player_id, accept=payload.accept)
        )

    return router


def device_token(payload: TokenRequest, request: Request) -> JSONResponse:
    """The board's poll : an RFC 8628 error, or the ``device`` token once."""
    wait = POLLS.retry_after(request.client.host if request.client else "")
    if wait is not None:
        raise HTTPException(429, TOO_MANY_POLLS, headers={"Retry-After": str(wait)})
    try:
        player_id, device_name = PAIRINGS.poll(payload.device_code)
    except pairing.PairingError as exc:
        return _rfc_error(exc.error)
    try:
        token = registry.issue_token(
            _games_dir(), player_id, label=f"échiquier {device_name}", token_class="device"
        )
    except registry.RegistryError as exc:
        # The yes is lost with the pairing : the board asks again, nothing is half made.
        log.warning("/api/device/token : registry unreadable (%s)", exc)
        raise HTTPException(503, REGISTRY_UNREADABLE) from exc
    return JSONResponse(
        {"access_token": token, "token_type": "Bearer", "player": player_id},
        headers={"Cache-Control": "no-store"},
    )


def _pairing_player(request: Request) -> registry.TokenUse:
    """The request's ``api`` or ``browser`` token ; 401 or 503 otherwise."""
    token = bearer_token(request.headers)
    try:
        use = registry.verify(_games_dir(), token, PAIRING_CLASSES) if token else None
    except registry.RegistryError as exc:
        log.warning("pairing : registry unreadable (%s)", exc)
        raise HTTPException(503, REGISTRY_UNREADABLE) from exc
    if use is None:
        raise HTTPException(401, UNAUTHENTICATED, headers={"WWW-Authenticate": "Bearer"})
    return use


def _with_code(use: registry.TokenUse, step: Callable[[], dict]) -> dict:
    """Run the player's step on a code ; a wrong code counted, five a minute at most."""
    wait = WRONG_CODES.waiting(use.player_id)
    if wait is not None:
        raise HTTPException(429, TOO_MANY_CODES, headers={"Retry-After": str(wait)})
    try:
        return step()
    except pairing.PairingError as exc:
        WRONG_CODES.retry_after(use.player_id)
        raise HTTPException(404, UNKNOWN_CODE) from exc
