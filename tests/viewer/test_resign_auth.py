"""Viewer resignation with the player's own token (Chessnut bridge, king lifted off).

Same rule as the MCP tool ``chess_resign`` : either player may resign,
whoever is to move ; the side is the token's, never a payload field.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState
from palamede_chessboard.players import registry


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid in ("stephane", "leader", "admin"):
        registry.add_player(games_dir, pid, kind="human" if pid == "stephane" else "agent")
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    GameState.create(
        GameMetadata(game_id="exo", white="leader", black="stephane"),
        games_dir=games_dir,
    )
    return issued


def _resign(client: TestClient, token: str | None, game: str = "exo"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(f"/api/games/{game}/resign", headers=headers)


def _result(games_dir: Path, game: str = "exo") -> str:
    return GameState.load(game, games_dir=games_dir).metadata.result


def test_black_resigns_while_white_is_to_move(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    r = _resign(client, tokens["stephane"])
    assert r.status_code == 200
    assert r.json()["resign"]["resigned_by"] == "black"
    assert _result(games_dir) == "1-0"
    assert GameState.load("exo", games_dir=games_dir).metadata.termination_note == (
        "stephane resigns"
    )


def test_white_resigns_its_own_side(client: TestClient, tokens: dict, games_dir: Path) -> None:
    r = _resign(client, tokens["leader"])
    assert r.status_code == 200 and r.json()["resign"]["resigned_by"] == "white"
    assert _result(games_dir) == "0-1"


def test_not_a_player_is_refused(client: TestClient, tokens: dict, games_dir: Path) -> None:
    r = _resign(client, tokens["admin"])
    assert r.status_code == 403
    assert "NOT_A_PLAYER" in r.json()["detail"]
    assert _result(games_dir) == "*"


@pytest.mark.parametrize("token", [None, "pas-un-jeton"], ids=["absent", "malformé"])
def test_missing_or_invalid_token_is_401(
    client: TestClient, tokens: dict, games_dir: Path, token
) -> None:
    r = _resign(client, token)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert _result(games_dir) == "*"


def test_finished_game_is_409(client: TestClient, tokens: dict, games_dir: Path) -> None:
    assert _resign(client, tokens["stephane"]).status_code == 200
    r = _resign(client, tokens["leader"])
    assert r.status_code == 409
    assert _result(games_dir) == "1-0"


def test_unknown_game_is_404(client: TestClient, tokens: dict) -> None:
    assert _resign(client, tokens["stephane"], game="ghost").status_code == 404
