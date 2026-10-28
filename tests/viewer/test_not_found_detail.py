"""A missing game's 404 names the game, never the PGN path on the server (#104).

:class:`GameNotFoundError` carries the absolute path ; every route that
loads a game must answer with the id only, token or not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROUTES = [
    ("GET", "/api/raw?game_id=ghost", None),
    ("GET", "/api/board.svg?game_id=ghost", None),
    ("GET", "/api/positions?game_id=ghost", None),
    ("GET", "/api/engine/live?game_id=ghost", None),
    ("GET", "/api/games/ghost/seat", None),
    ("POST", "/api/games/ghost/move", {"san": "e4"}),
    ("POST", "/api/games/ghost/resign", None),
]


@pytest.mark.parametrize(("method", "url", "body"), ROUTES, ids=[r[1] for r in ROUTES])
def test_404_names_the_game_only(
    client: TestClient, games_dir: Path, method: str, url: str, body: dict | None
) -> None:
    r = client.request(method, url, json=body)
    assert r.status_code == 404
    assert r.json() == {"detail": "Partie inconnue : ghost"}
    assert str(games_dir) not in r.text and ".pgn" not in r.text
