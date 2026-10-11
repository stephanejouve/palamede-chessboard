"""Shared models, constants and helpers for the viewer routers.

BT-007 split (issue #12). The 568-line ``chessboard_viewer.py`` god
script was carved into one ``APIRouter`` per domain
(``games``/``positions``/``realtime``/``elo``) plus a thin
``build_app`` assembler. All shared pieces live here :

* role whitelists (``VIEWER_ROLES``, ``ACCEPTED_VIEWERS``,
  ``PLAYER_VIEWERS``), kept honor-system per PR #122 follow-up ;
* UI constants (``DEPTH_OPTIONS``, ``TEMPLATES_DIR``) ;
* SSE timings (``SSE_HEARTBEAT_SECONDS`` /
  ``SSE_POLL_INTERVAL_SECONDS``) ;
* pydantic payloads (``CommentaryPayload``, ``MovePayload``) ;
* internal helpers (``_games_dir``, ``_game_stream_payload``).

The module is intentionally dependency-light (chess + pydantic +
palamede state only) — importing it must NOT drag FastAPI in, so
scripts that only need :func:`_game_stream_payload` for probes stay
cheap.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import chess
from pydantic import BaseModel

from palamede_chessboard.chessboard_state import GAMES_DIR, GameState

#: Roles declarable as ``viewer`` query param.
#:
#: Honor system (PR #122 follow-up review #121) : ``viewer`` is
#: client-declared, the server does not verify it (no signed token /
#: no auth). A dishonest player can self-declare ``spectator`` or
#: ``arbiter`` to bypass the 403. The effective server-side gate is
#: purely ``is_over`` : if the game is live, roles ``player_white`` /
#: ``player_black`` are 403'd ; otherwise everyone has access.
#:
#: To turn into real auth (exit honor system) : add an HMAC token
#: signed by the MCP server at game creation, distributed to players
#: via the ``chess_create_game`` tool, verified viewer-side in a
#: ``X-Chessboard-Token`` header. Separate T-id.
VIEWER_ROLES: tuple[str, ...] = (
    "spectator",
    "arbiter",
    "player_white",
    "player_black",
    "post_mortem",
)
ACCEPTED_VIEWERS: set[str] = set(VIEWER_ROLES)
PLAYER_VIEWERS: set[str] = {"player_white", "player_black"}

#: Depth values exposed in the engine analysis <select> dropdown.
#: ``DEFAULT_DEPTH`` (from ``chessboard_engine``) is pre-selected.
DEPTH_OPTIONS: tuple[int, ...] = (8, 12, 15, 20)

#: ``templates/`` directory relative to the ``palamede_chessboard``
#: package root (one dir up from ``viewer/``).
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

#: SSE heartbeat interval (seconds). DSM reverse proxies typically close
#: idle connections after 60s ; a 15s keepalive stays well under that.
SSE_HEARTBEAT_SECONDS = 15.0
#: Polling interval (seconds) for detecting PGN mtime changes inside the
#: SSE generator. 500ms is imperceptible to humans and cheap on I/O.
SSE_POLL_INTERVAL_SECONDS = 0.5


def _games_dir() -> Path:
    """Return the games directory (env override honored for tests)."""
    raw = os.environ.get("MCP_CHESSBOARD_GAMES_DIR", "").strip()
    return Path(raw) if raw else GAMES_DIR


class CommentaryPayload(BaseModel):
    ply: int
    source: str
    text: str


class MovePayload(BaseModel):
    """Payload for ``POST /api/games/<id>/move``.

    Either ``san`` or ``uci`` must be provided. ``uci`` is server-side
    converted to SAN before validation, so promotion suffixes (``e7e8q``)
    are supported without a dedicated UI dialog. ``by`` is audit-only.
    """

    san: str | None = None
    uci: str | None = None
    by: str | None = None


def _game_stream_payload(state: GameState) -> dict[str, Any]:
    """Compact snapshot pushed on every SSE event.

    Kept small — clients that need the full ``positions`` array can
    still hit ``/api/positions`` on the same game_id ; the SSE event is
    a change notification, not a full replay dump.
    """
    return {
        "game_id": state.metadata.game_id,
        "fen": state.board.fen(),
        "ply": state.board.ply(),
        "turn": "white" if state.board.turn == chess.WHITE else "black",
        "result": state.metadata.result,
        "san_history": list(state.san_history),
        "is_game_over": state.board.is_game_over() or state.metadata.result != "*",
    }
