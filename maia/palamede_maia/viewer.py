"""The few viewer routes the bot needs, over HTTP with the standard library.

Every call returns ``None`` (or ``False``) on a network error or an
unexpected answer : the bot logs it and tries again on its next round,
it never crashes on a viewer that restarts.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)

TIMEOUT = 10


def _request(url: str, *, data: dict | None = None, token: str | None = None) -> tuple[int, Any]:
    headers = {"Accept": "application/json", "User-Agent": "palamede-maia"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, None


def games_in_progress(viewer: str) -> list[dict] | None:
    """``in_progress`` of ``GET /api/games`` ; ``None`` if the viewer cannot say."""
    try:
        status, body = _request(f"{viewer}/api/games?limit=0")
    except (OSError, ValueError) as exc:
        log.warning("listing unreachable : %s", exc.__class__.__name__)
        return None
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("in_progress"), list):
        log.warning("listing : unexpected answer (HTTP %s)", status)
        return None
    return [g for g in body["in_progress"] if isinstance(g, dict)]


def last_fen(viewer: str, game_id: str) -> str | None:
    """FEN of the current position, from ``GET /api/positions``."""
    try:
        status, body = _request(f"{viewer}/api/positions?game_id={quote(game_id)}")
    except (OSError, ValueError) as exc:
        log.warning("%s : positions unreachable (%s)", game_id, exc.__class__.__name__)
        return None
    positions = body.get("positions") if status == 200 and isinstance(body, dict) else None
    if not positions or not isinstance(positions[-1], dict):
        log.warning("%s : no position (HTTP %s)", game_id, status)
        return None
    fen = positions[-1].get("fen")
    return fen if isinstance(fen, str) else None


def play(viewer: str, game_id: str, uci: str, token: str) -> int | None:
    """``POST /api/games/<id>/move`` with the bot's token ; the HTTP status, ``None`` if unreachable."""
    try:
        status, _ = _request(
            f"{viewer}/api/games/{quote(game_id)}/move", data={"uci": uci}, token=token
        )
    except (OSError, ValueError) as exc:
        log.warning("%s : move %s not sent (%s)", game_id, uci, exc.__class__.__name__)
        return None
    return status
