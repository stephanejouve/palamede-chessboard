"""Token check of the Tablier app at launch (#78, step A) : who, which side.

Nothing is played : the game is unchanged whatever the answer.
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
    GameState.create(
        GameMetadata(game_id="solo", white="stephane", black="stephane"),
        games_dir=games_dir,
    )
    return issued


def _seat(client: TestClient, token: str | None, game: str = "exo"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(f"/api/games/{game}/seat", headers=headers)


def _unchanged(games_dir: Path, game: str = "exo") -> bool:
    state = GameState.load(game, games_dir=games_dir)
    return state.board.ply() == 0 and state.metadata.result == "*"


@pytest.mark.parametrize(("pid", "color"), [("leader", "white"), ("stephane", "black")])
def test_player_learns_their_side(
    client: TestClient, tokens: dict, games_dir: Path, pid: str, color: str
) -> None:
    r = _seat(client, tokens[pid])
    assert r.status_code == 200
    assert r.json() == {"game_id": "exo", "player": pid, "colors": [color]}
    assert r.headers["cache-control"] == "no-store"
    assert _unchanged(games_dir)


def test_both_sides_when_playing_oneself(client: TestClient, tokens: dict) -> None:
    r = _seat(client, tokens["stephane"], game="solo")
    assert r.json()["colors"] == ["white", "black"]


def test_whoever_is_to_move(client: TestClient, tokens: dict, games_dir: Path) -> None:
    """Unlike a move, the side not to move gets its answer too."""
    assert _seat(client, tokens["stephane"]).status_code == 200


def test_not_a_player_is_403(client: TestClient, tokens: dict, games_dir: Path) -> None:
    r = _seat(client, tokens["admin"])
    assert r.status_code == 403
    assert "NOT_A_PLAYER" in r.json()["detail"]
    assert "admin" not in r.json()["detail"]


@pytest.mark.parametrize("token", [None, "pas-un-jeton"], ids=["absent", "malformé"])
def test_missing_or_invalid_token_is_401(client: TestClient, tokens: dict, token) -> None:
    r = _seat(client, token)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_token_never_in_the_answer(client: TestClient, tokens: dict) -> None:
    for token in (tokens["stephane"], tokens["admin"], "pas-un-jeton"):
        assert token not in _seat(client, token).text


def test_unknown_game_is_404(client: TestClient, tokens: dict) -> None:
    assert _seat(client, tokens["stephane"], game="ghost").status_code == 404
