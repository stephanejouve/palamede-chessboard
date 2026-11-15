"""A player's own tokens : list and revoke (#351 §6, PR 1).

Players named by function. Only the token's own player is listed or
revoked ; another player's token answers 404, like an unknown one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.players import registry
from palamede_chessboard.players.tokens import parse_token

HUMAN, OTHER = "human-player", "other-player"


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    registry.add_player(games_dir, HUMAN, kind="human")
    registry.add_player(games_dir, OTHER, kind="human")
    return {
        "api": registry.issue_token(games_dir, HUMAN, label="cli"),
        "browser": registry.issue_token(games_dir, HUMAN, label="laptop", token_class="browser"),
        "recovery": registry.issue_token(games_dir, HUMAN, label="spare", token_class="recovery"),
        "other": registry.issue_token(games_dir, OTHER, label="cli"),
    }


def _auth(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _parts(token: str) -> tuple[str, str]:
    # The secret itself may hold an underscore : read by the token parser.
    parsed = parse_token(token)
    assert parsed is not None
    return parsed


def _id(token: str) -> str:
    return _parts(token)[0]


def test_the_list_needs_an_account_token(client: TestClient, tokens: dict) -> None:
    assert client.get("/api/me/tokens").status_code == 401
    # A recovery token only opens a browser session (owner, 14/11) : not here.
    assert client.get("/api/me/tokens", headers=_auth(tokens["recovery"])).status_code == 401


def test_only_one_s_own_tokens_the_current_one_marked(client: TestClient, tokens: dict) -> None:
    r = client.get("/api/me/tokens", headers=_auth(tokens["browser"]))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["player"] == HUMAN
    listed = {t["id"]: t for t in body["tokens"]}
    assert set(listed) == {_id(tokens[k]) for k in ("api", "browser", "recovery")}
    assert listed[_id(tokens["browser"])]["current"] is True
    assert listed[_id(tokens["api"])]["current"] is False
    assert listed[_id(tokens["recovery"])]["class"] == "recovery"
    text = r.text
    for plain in tokens.values():
        assert _parts(plain)[1] not in text  # never a secret
    assert "sha256" not in text


def test_revoking_one_s_own_token_takes_effect_at_once(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    r = client.delete(f"/api/me/tokens/{_id(tokens['api'])}", headers=_auth(tokens["browser"]))
    assert r.status_code == 200
    assert r.json() == {"player": HUMAN, "revoked": _id(tokens["api"])}
    assert registry.verify_token(games_dir, tokens["api"]) is None
    assert client.get("/api/me", headers=_auth(tokens["api"])).status_code == 401
    # Revoking the request's own token works too, and it is dead after.
    r = client.delete(f"/api/me/tokens/{_id(tokens['browser'])}", headers=_auth(tokens["browser"]))
    assert r.status_code == 200
    assert client.get("/api/me/tokens", headers=_auth(tokens["browser"])).status_code == 401


def test_another_player_s_token_is_a_404_and_stays_valid(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    r = client.delete(f"/api/me/tokens/{_id(tokens['other'])}", headers=_auth(tokens["api"]))
    assert r.status_code == 404
    assert registry.verify_token(games_dir, tokens["other"]) == OTHER
    assert client.delete("/api/me/tokens/00000000", headers=_auth(tokens["api"])).status_code == 404


def test_revoking_needs_an_account_token(client: TestClient, tokens: dict) -> None:
    target = f"/api/me/tokens/{_id(tokens['api'])}"
    assert client.delete(target).status_code == 401
    assert client.delete(target, headers=_auth(tokens["recovery"])).status_code == 401
    assert client.get("/api/me", headers=_auth(tokens["api"])).status_code == 200


def test_an_unreadable_registry_is_a_503(client: TestClient, tokens: dict, games_dir: Path) -> None:
    registry.registry_path(games_dir).write_text("{", encoding="utf-8")
    assert client.get("/api/me/tokens", headers=_auth(tokens["api"])).status_code == 503
