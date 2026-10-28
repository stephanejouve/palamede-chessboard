"""The viewer's HTTP API, as the bridge uses it : standard library only."""

from __future__ import annotations

import http.client
import json
import logging
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

import chess

log = logging.getLogger(__name__)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirected POST is reported, not followed (review Admin #71).

    Following it would send the move nowhere useful while the bridge
    counts it as delivered ; the 3xx comes back as an ``HTTPError``.
    """

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def post(url: str, token: str, payload: dict[str, Any] | None = None) -> tuple[int | None, str]:
    """POST with the player token : ``(status, body excerpt)``, status ``None`` if unreachable.

    The token travels in an unredirected header : even a redirect that
    some other opener followed would not carry it (review Admin #71).
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with _OPENER.open(request, timeout=10) as response:
            return int(response.status), ""
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:200]
    except OSError as exc:
        return None, str(exc)


def get_with_token(url: str, token: str) -> tuple[int | None, Any]:
    """GET with the player token : ``(status, JSON body or None)``, status ``None`` if unreachable.

    Same unredirected header as :func:`post` ; a 3xx is reported, not followed.
    """
    request = urllib.request.Request(url, method="GET")
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with _OPENER.open(request, timeout=10) as response:
            return int(response.status), json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (OSError, ValueError, http.client.HTTPException):
        # A malformed answer (BadStatusLine, IncompleteRead) is no answer :
        # the light stays orange and the bridge starts (review Junior #106).
        return None, None


def get_json(url: str) -> Any:
    """GET a JSON document (no token) ; raises ``OSError`` / ``ValueError``."""
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def position(viewer_url: str, game_id: str) -> dict[str, Any] | None:
    """The game's position on the viewer, shaped as a stream snapshot ; ``None`` if unreadable.

    With its moves (``san_history``), so that catching up with it keeps the
    move list (review Admin #97).
    """
    try:
        data = get_json(f"{viewer_url}/api/positions?game_id={quote(game_id)}")
        positions = data["positions"]
        fen = str(positions[-1]["fen"])
        over = data.get("result", "*") != "*" or chess.Board(fen).is_game_over()
        history = [str(p["san"]) for p in positions[1:] if p.get("san")]
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        log.warning("position of %s unreadable (%s)", game_id, exc)
        return None
    return {"fen": fen, "ply": "?", "turn": "?", "is_game_over": over, "san_history": history}
