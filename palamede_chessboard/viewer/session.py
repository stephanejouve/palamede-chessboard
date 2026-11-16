"""The browser's session (#351, PR 2) : the player token in an HttpOnly cookie.

The token pasted with 🔑 is sent once to ``POST /api/session`` ; the server
answers with a new ``browser`` token in the :data:`COOKIE_NAME` cookie,
``HttpOnly`` (no script reads it), ``Secure``, ``SameSite=Lax``, kept
:data:`COOKIE_MAX_AGE` seconds after the browser closes (owner, 14/11). The
page never holds a token.

:class:`SessionMiddleware` turns the cookie into the ``Authorization:
Bearer`` header every route already reads, so no route changes :

* a request that carries ``Authorization`` keeps it : the Bearer wins, and
  an invalid one is a 401, never a fall back to the cookie (review Admin
  #351, point 4) ;
* a request authenticated by the cookie that changes something (POST, PUT,
  PATCH, DELETE) must come from this page : its ``Origin`` must name the
  host the browser asked, else 403 before any route runs. ``SameSite=Lax``
  already keeps the cookie off a cross-site POST ; this check covers the
  browsers that do not apply it (design #351 §2).

``PALAMEDE_COOKIE_INSECURE=1`` drops ``Secure`` for an install served over
plain HTTP on a local network ; Chrome and Firefox keep a ``Secure`` cookie
on ``http://localhost`` and ``http://127.0.0.1`` without it.
"""

from __future__ import annotations

import os
import time
from collections import deque
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from palamede_chessboard.players import tokens

COOKIE_NAME = "palamede_session"
#: 400 days, the most a browser keeps a cookie ; a ``browser`` token unused
#: for 90 days is refused before (registry.BROWSER_IDLE_DAYS).
COOKIE_MAX_AGE = 400 * 24 * 3600
INSECURE_ENV = "PALAMEDE_COOKIE_INSECURE"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CROSS_SITE = "Requête refusée : elle ne vient pas de la page du viewer"
_MAX_KEYS = 10_000

Headers = list[tuple[bytes, bytes]]


def cookie_secure() -> bool:
    return os.environ.get(INSECURE_ENV, "").strip() != "1"


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        path="/",
        secure=cookie_secure(),
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    """Expire the cookie (``Max-Age=0``) ; the token itself stays valid."""
    response.delete_cookie(
        COOKIE_NAME, path="/", secure=cookie_secure(), httponly=True, samesite="lax"
    )


def _header(headers: Headers, name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def cookie_token(headers: Headers) -> str | None:
    """The session cookie's token, if it has the form of one ; else ``None``."""
    raw = _header(headers, b"cookie")
    if not raw:
        return None
    try:
        jar = SimpleCookie(raw)
    except CookieError:
        return None
    morsel = jar.get(COOKIE_NAME)
    if morsel is None or tokens.parse_token(morsel.value) is None:
        return None
    return morsel.value


def same_origin(headers: Headers) -> bool:
    """The request's ``Origin`` names the host the browser asked.

    The host is ``Host``, or ``X-Forwarded-Host`` behind the reverse
    proxy. A page of another site cannot set either on a request it sends :
    a form cannot add headers, and a ``fetch`` with a custom header needs a
    CORS answer the viewer never gives.
    """
    origin = _header(headers, b"origin")
    if not origin or origin == "null":
        return False
    netloc = urlsplit(origin).netloc.lower()
    hosts = {
        (_header(headers, b"host") or "").lower(),
        (_header(headers, b"x-forwarded-host") or "").split(",")[0].strip().lower(),
    }
    return bool(netloc) and netloc in hosts


class SessionMiddleware:
    """The cookie as the request's Bearer token ; cross-site writes refused."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: Headers = scope["headers"]
        token = None if _header(headers, b"authorization") else cookie_token(headers)
        if token is None:
            await self.app(scope, receive, send)
            return
        if scope["method"] in UNSAFE_METHODS and not same_origin(headers):
            await JSONResponse({"detail": CROSS_SITE}, status_code=403)(scope, receive, send)
            return
        scope = {
            **scope,
            "headers": [*headers, (b"authorization", f"Bearer {token}".encode("latin-1"))],
            "state": {**scope.get("state", {}), "session_cookie": True},
        }
        await self.app(scope, receive, send)


class RateLimit:
    """At most ``limit`` calls per ``seconds`` and per key, in this process.

    The viewer runs one uvicorn worker (``viewer/app.py``) : the counters
    are those of the only process. With several workers, a shared counter
    would be needed. A restart empties them, which an attacker cannot do
    (review Admin #351, point 3).
    """

    def __init__(self, limit: int, seconds: float) -> None:
        self.limit, self.seconds = limit, seconds
        self._calls: dict[str, deque[float]] = {}

    def waiting(self, key: str, now: float | None = None) -> int | None:
        """The seconds to wait, or ``None`` ; the call is **not** counted."""
        now = time.monotonic() if now is None else now
        if len(self._calls) > _MAX_KEYS:  # keys of past windows only : memory stays bounded
            self._calls = {k: c for k, c in self._calls.items() if c and now - c[-1] < self.seconds}
        calls = self._calls.setdefault(key, deque())
        while calls and now - calls[0] >= self.seconds:
            calls.popleft()
        if len(calls) >= self.limit:
            return max(1, int(self.seconds - (now - calls[0])) + 1)
        return None

    def retry_after(self, key: str, now: float | None = None) -> int | None:
        """``None`` and the call counted, or the seconds to wait."""
        now = time.monotonic() if now is None else now
        wait = self.waiting(key, now)
        if wait is None:
            self._calls[key].append(now)
        return wait
