"""A launch without ``--game`` nor ``--color`` (#90) : the token says the rest.

The account comes from the viewer's ``GET /api/me`` (the token is the only
truth about it, review Admin and Leader #90), the game is the account's
newest in progress (``GET /api/games?player=``, newest first), and the side
is the one the account holds in it (``/seat``, as « Fichier ▸ Ouvrir »).
Another game is then chosen in the window. Each failure exits with what to
do : no game is ever guessed past a refusal.
"""

from __future__ import annotations

from urllib.parse import quote

import chess

from palamede_chessnut import viewer
from palamede_chessnut.games import GAME_ID, chosen_color, player_games

UNREACHABLE = "{viewer} does not answer : check the URL, or retry in a moment"
REFUSED = "the viewer refuses the player token (HTTP {status}) : check the token file"


def account_of_token(viewer_url: str, token: str) -> str:
    """The token's account, from ``/api/me`` ; exits when the viewer cannot say."""
    status, body = viewer.get_with_token(f"{viewer_url}/api/me", token)
    if status is None:
        raise SystemExit(UNREACHABLE.format(viewer=viewer_url))
    if status in (401, 403):
        raise SystemExit(REFUSED.format(status=status))
    if status == 404:
        raise SystemExit(f"{viewer_url} is too old for a launch without --game : give --game")
    account = body.get("player") if status == 200 and isinstance(body, dict) else None
    if not isinstance(account, str) or not account:
        raise SystemExit(f"{viewer_url} : unexpected answer to /api/me (HTTP {status})")
    return account


def newest_game(viewer_url: str, account: str) -> str:
    """The account's newest game in progress ; exits when there is none."""
    entries = player_games(viewer_url, account)
    if entries is None:
        raise SystemExit(UNREACHABLE.format(viewer=viewer_url))
    for entry in entries:
        # The viewer's own rule : a name it could not have given is skipped.
        if GAME_ID.fullmatch(entry["game_id"]):
            return entry["game_id"]
    raise SystemExit(f"no game in progress for {account} : start one, or give --game")


def seat_color(viewer_url: str, game_id: str, token: str) -> chess.Color:
    """The side the token's account plays in ``game_id`` ; white when it plays both."""
    status, body = viewer.get_with_token(f"{viewer_url}/api/games/{quote(game_id)}/seat", token)
    if status is None:
        raise SystemExit(UNREACHABLE.format(viewer=viewer_url))
    if status == 401:
        raise SystemExit(REFUSED.format(status=status))
    if status == 403:
        raise SystemExit(f"your account does not play {game_id}")
    if status == 404:
        raise SystemExit(f"{game_id} : no such game on {viewer_url}")
    colors = body.get("colors") if status == 200 and isinstance(body, dict) else None
    color = chosen_color(list(colors or []), chess.WHITE)
    if color is None:
        raise SystemExit(f"{viewer_url} : no side for you in {game_id} (HTTP {status})")
    return color
