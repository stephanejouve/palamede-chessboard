"""Game creation from a player's token, ``POST /api/games`` (#93, PR 1).

Only unrated games, the creator in one of the two seats, an existing
opponent, a server-chosen id, and no arbiter (Leader, #93, 29/10).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameState
from palamede_chessboard.players import registry


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid, kind in (("stephane", "human"), ("leader", "agent"), ("bot-maia-1500", "agent")):
        registry.add_player(games_dir, pid, kind=kind)
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    return issued


def _create(client: TestClient, token: str | None, **body):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/games", json={"opponent": "leader", **body}, headers=headers)


def _pgns(games_dir: Path) -> list[Path]:
    return sorted(games_dir.glob("*.pgn"))


@pytest.mark.parametrize(
    ("color", "white", "black"), [("white", "stephane", "leader"), ("black", "leader", "stephane")]
)
def test_creator_gets_the_chosen_side(
    client: TestClient, tokens: dict, games_dir: Path, color: str, white: str, black: str
) -> None:
    r = _create(client, tokens["stephane"], color=color)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["color"] == color
    state = GameState.load(body["game_id"], games_dir=games_dir)
    assert (state.metadata.white, state.metadata.black) == (white, black)
    assert state.metadata.rated is False
    assert state.metadata.arbiter is None
    assert state.metadata.event == "Tablier"
    assert state.board.ply() == 0


def test_random_color_puts_the_creator_in_a_seat(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    for _ in range(6):
        body = _create(client, tokens["stephane"]).json()
        state = GameState.load(body["game_id"], games_dir=games_dir)
        assert getattr(state.metadata, body["color"]) == "stephane"
        assert {state.metadata.white, state.metadata.black} == {"stephane", "leader"}


def test_game_id_is_chosen_by_the_server(client: TestClient, tokens: dict) -> None:
    r = _create(client, tokens["stephane"], game_id="../../etc/passwd")
    assert re.fullmatch(r"tablier-\d{8}-\d{6}-[0-9a-f]{4}", r.json()["game_id"])


def test_rated_game_is_refused(client: TestClient, tokens: dict, games_dir: Path) -> None:
    """Leader : a rated game keeps the arbiter contract of chess_create_game."""
    r = _create(client, tokens["stephane"], rated=True)
    assert r.status_code == 400
    assert "arbitre" in r.json()["detail"]
    assert _pgns(games_dir) == []


@pytest.mark.parametrize("rated", ["false", 0, None, "true"])
def test_rated_must_be_a_strict_boolean(
    client: TestClient, tokens: dict, games_dir: Path, rated
) -> None:
    r = _create(client, tokens["stephane"], rated=rated)
    assert r.status_code == 400
    assert _pgns(games_dir) == []


def test_bot_opponent_is_unrated(client: TestClient, tokens: dict, games_dir: Path) -> None:
    r = _create(client, tokens["stephane"], opponent="bot-maia-1500", rated=False)
    assert r.status_code == 201
    assert GameState.load(r.json()["game_id"], games_dir=games_dir).metadata.rated is False


@pytest.mark.parametrize("token", [None, "pas-un-jeton"], ids=["absent", "malformé"])
def test_missing_or_invalid_token_is_401(
    client: TestClient, tokens: dict, games_dir: Path, token
) -> None:
    r = _create(client, token)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert _pgns(games_dir) == []


@pytest.mark.parametrize(
    "opponent", ["inconnu", "Stéphane!", "stephane"], ids=["inconnu", "invalide", "soi-même"]
)
def test_bad_opponent_is_422(
    client: TestClient, tokens: dict, games_dir: Path, opponent: str
) -> None:
    r = _create(client, tokens["stephane"], opponent=opponent)
    assert r.status_code == 422
    assert _pgns(games_dir) == []


def test_opponent_name_is_normalised(client: TestClient, tokens: dict, games_dir: Path) -> None:
    r = _create(client, tokens["stephane"], opponent="Leader", color="white")
    assert GameState.load(r.json()["game_id"], games_dir=games_dir).metadata.black == "leader"


def test_time_control_starts_a_clock(client: TestClient, tokens: dict) -> None:
    r = _create(client, tokens["stephane"], time_control={"format": "5+3"})
    assert r.json()["clock"] is not None


def test_no_clock_by_default(client: TestClient, tokens: dict) -> None:
    assert _create(client, tokens["stephane"]).json()["clock"] is None


def test_token_never_in_the_answer(client: TestClient, tokens: dict) -> None:
    r = _create(client, tokens["stephane"])
    assert tokens["stephane"] not in r.text


def test_unreadable_registry_is_503(client: TestClient, tokens: dict, games_dir: Path) -> None:
    registry.registry_path(games_dir).write_text("{pas du json")
    r = _create(client, tokens["stephane"])
    assert r.status_code == 503
    assert _pgns(games_dir) == []


def test_new_game_can_be_played_by_its_creator(client: TestClient, tokens: dict) -> None:
    """The created game is an ordinary one : the creator's token plays in it."""
    game_id = _create(client, tokens["stephane"], color="white").json()["game_id"]
    r = client.post(
        f"/api/games/{game_id}/move",
        json={"san": "e4"},
        headers={"Authorization": f"Bearer {tokens['stephane']}"},
    )
    assert r.status_code == 200, r.text
