"""Viewer moves need the player's own token (#34, demand of CD 59126).

Stéphane plays from the viewer with his token, issued by the registry
(#54) : the move is attributed to « stephane » and refused when it is
not his turn, not his game, or without a valid token.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState
from palamede_chessboard.players import registry

FEN_BLACK_TO_MOVE = "4k3/8/8/8/8/8/4P3/4K3 b - - 0 1"


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid in ("stephane", "claude-desktop", "leader"):
        registry.add_player(games_dir, pid, kind="human" if pid == "stephane" else "agent")
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    GameState.create(
        GameMetadata(game_id="exo", white="stephane", black="claude-desktop"),
        games_dir=games_dir,
    )
    return issued


def _move(client: TestClient, token: str | None, san: str = "e4", game: str = "exo"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(f"/api/games/{game}/move", json={"san": san}, headers=headers)


def test_player_to_move_plays_and_is_attributed(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    r = _move(client, tokens["stephane"])
    assert r.status_code == 200
    assert r.json()["move"]["by"] == "stephane"
    assert GameState.load("exo", games_dir=games_dir).san_history == ["e4"]


def test_wrong_side_is_refused(client: TestClient, tokens: dict, games_dir: Path) -> None:
    r = _move(client, tokens["claude-desktop"])  # les Blancs sont au trait
    assert r.status_code == 403
    assert "NOT_YOUR_TURN" in r.json()["detail"]
    assert GameState.load("exo", games_dir=games_dir).san_history == []


def test_not_a_player_is_refused(client: TestClient, tokens: dict) -> None:
    r = _move(client, tokens["leader"])
    assert r.status_code == 403
    assert "NOT_A_PLAYER" in r.json()["detail"]


@pytest.mark.parametrize(
    "token",
    [None, "plm_00000000_" + "a" * 43, "pas-un-jeton"],
    ids=["absent", "inconnu", "malformé"],
)
def test_missing_or_invalid_token_is_401(
    client: TestClient, tokens: dict, games_dir: Path, token
) -> None:
    r = _move(client, token)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert "UNAUTHENTICATED" in r.json()["detail"]
    assert GameState.load("exo", games_dir=games_dir).san_history == []


def test_revoked_token_is_refused(client: TestClient, tokens: dict, games_dir: Path) -> None:
    registry.revoke_token(games_dir, "stephane", tokens["stephane"].split("_")[1])
    assert _move(client, tokens["stephane"]).status_code == 401


def test_black_plays_when_black_is_to_move(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    GameState.create(
        GameMetadata(game_id="noirs", white="stephane", black="claude-desktop"),
        games_dir=games_dir,
        starting_fen=FEN_BLACK_TO_MOVE,
    )
    assert _move(client, tokens["stephane"], "Kd7", "noirs").status_code == 403
    r = _move(client, tokens["claude-desktop"], "Kd7", "noirs")
    assert r.status_code == 200 and r.json()["move"]["by"] == "claude-desktop"


def test_unreadable_registry_refuses_with_503(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    registry.registry_path(games_dir).write_text("{cassé", encoding="utf-8")
    r = _move(client, tokens["stephane"])
    assert r.status_code == 503
    assert "REGISTRY_UNREADABLE" in r.json()["detail"]
    assert GameState.load("exo", games_dir=games_dir).san_history == []


def test_unknown_game_is_still_404(client: TestClient, tokens: dict) -> None:
    assert _move(client, tokens["stephane"], game="ghost").status_code == 404
