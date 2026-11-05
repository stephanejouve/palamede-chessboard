"""What the window says of a move the viewer refused, in the viewer's own words (#226).

The bridge keeps the viewer's answer as ``HTTP <code> <body>`` ; the body of
a refusal is ``{"detail": ...}`` : « Ce n'est pas le coup » for an exercise,
« Coup refusé (CODE) » for the rules. Shared by the board (:mod:`.status`)
and the mouse (:mod:`.mouse`), which say it the same way.
"""

from __future__ import annotations

import json


def refusal_said(reason: str | None) -> str:
    """« Coup refusé : Ce n'est pas le coup », from ``HTTP 409 {"detail": ...}``."""
    _status, _, body = (reason or "").partition(" ")
    _code, _, body = body.partition(" ")
    try:
        detail = json.loads(body).get("detail")
    except (ValueError, AttributeError):
        detail = None
    said = detail.strip().rstrip(".") if isinstance(detail, str) and detail.strip() else ""
    # The viewer's default 403 starts with « Coup refusé » itself (review Junior #226).
    if not said.lower().startswith("coup refusé"):
        said = "Coup refusé : " + (said or "le serveur l'a refusé")
    return said
