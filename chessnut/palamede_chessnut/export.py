"""The game's PGN for the page (#134) : shown, copied, or saved to a file.

The bridge reads the viewer's ``GET /api/raw``, a public route : the player
token never goes there. The page gets ``(status, body)`` ready to answer ;
saving asks the app where to write, through a native dialog.
"""

from __future__ import annotations

import logging
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from palamede_chessnut import viewer

log = logging.getLogger(__name__)

#: Where to save ``<name>`` : the chosen path, or ``None`` when cancelled.
AskPath = Callable[[str], str | None]

UNKNOWN = "Partie inconnue du viewer."
UNREACHABLE = "Le viewer ne répond pas."
UNREADABLE = "Réponse illisible du viewer."


def raw_pgn(viewer_url: str, game_id: str) -> tuple[int, dict[str, Any]]:
    """``(200, {game_id, pgn})``, or an error status with a French message."""
    try:
        body = viewer.get_json(f"{viewer_url}/api/raw?game_id={quote(game_id)}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 404, {"error": UNKNOWN}
        log.warning("PGN of %s : HTTP %s", game_id, exc.code)
        return 502, {"error": UNREACHABLE}
    except (OSError, ValueError) as exc:
        log.warning("PGN of %s unreadable (%s)", game_id, exc.__class__.__name__)
        return 502, {"error": UNREACHABLE}
    pgn = body.get("pgn") if isinstance(body, dict) else None
    if not isinstance(pgn, str):
        return 502, {"error": UNREADABLE}
    return 200, {"game_id": game_id, "pgn": pgn}


def save_pgn(viewer_url: str, game_id: str, ask_path: AskPath) -> tuple[int, dict[str, Any]]:
    """Read the PGN, ask where to write ``<game_id>.pgn``, write it.

    ``{"saved": None}`` when the dialog is cancelled ; the PGN is read before
    the dialog opens, so a viewer that does not answer asks nothing.
    """
    status, body = raw_pgn(viewer_url, game_id)
    if status != 200:
        return status, body
    chosen = ask_path(f"{game_id}.pgn")
    if not chosen:
        return 200, {"saved": None}
    path = Path(chosen)
    try:
        path.write_text(body["pgn"], encoding="utf-8")
    except OSError as exc:
        log.warning("PGN not saved to %s (%s)", path, exc.__class__.__name__)
        return 500, {"error": f"Impossible d'écrire {path.name}."}
    log.info("PGN of %s saved to %s", game_id, path)
    return 200, {"saved": path.name}
