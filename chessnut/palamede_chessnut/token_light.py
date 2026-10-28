"""The player token's light in the app (#78, step A).

The token is checked at launch, through the viewer's ``GET
/api/games/{id}/seat``, instead of at the first move : the smoke of #97
found a dead token only by a 401 in the Terminal. The page sees the light
and the account name, never the token nor its fingerprint ; the log gets
the fingerprint once (frame of Admin, 28/10).
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from palamede_chessnut.bridge import Bridge

log = logging.getLogger(__name__)

#: Lights that forbid playing : the server would refuse the moves.
REFUSING = frozenset({"refused", "not_player", "wrong_color"})
SIDES = {"white": "Blancs", "black": "Noirs"}


def fingerprint(token: str) -> str:
    """Length and 8 hex digits of the SHA-256 of the whole token : not a prefix of it."""
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
    return f"{len(token)} characters, sha256 {digest}"


def check(bridge: Bridge) -> None:
    """Set :attr:`Bridge.token_light` and :attr:`Bridge.account` from the viewer's answer."""
    status, seat = bridge.seat()
    color = "white" if bridge.player else "black"
    if status == 200 and isinstance(seat, dict):
        bridge.account = str(seat.get("player") or "") or None
        colors = seat.get("colors") or []
        bridge.token_light = "on" if color in colors else "wrong_color"
    elif status == 401:
        bridge.token_light = "refused"
    elif status == 403:
        bridge.token_light = "not_player"
    else:
        # An older viewer (404) or none reachable : the first move will tell.
        bridge.token_light = "unchecked"
    log.info("player token : %s, %s", bridge.token_print, bridge.token_light)


def message(bridge: Bridge) -> str | None:
    """What the page says about the token, or ``None`` when all is well."""
    light = bridge.token_light
    if light == "refused":
        # The token is read once, at launch (review Admin #106).
        return (
            "Jeton refusé par le serveur : corrigez le fichier du jeton "
            "(bouton 🔑 du viewer), puis relancez l'app."
        )
    if light == "not_player":
        return "Ce jeton est valide, mais son compte ne joue pas cette partie."
    if light == "wrong_color":
        played = SIDES["white" if bridge.player else "black"]
        return f"Le compte {bridge.account} ne joue pas les {played} dans cette partie : vérifiez --color."
    if light == "unchecked":
        return (
            "Jeton pas encore vérifié : le serveur ne sait pas encore le faire, "
            "ou ne répond pas. Votre premier coup le vérifiera."
        )
    return None
