"""Who a token is, without a game (#90) : the Tablier app's launch, #93's creator."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.players import registry
from palamede_chessboard.players import tokens as token_format


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid, kind in (("stephane", "human"), ("leader", "agent")):
        registry.add_player(games_dir, pid, kind=kind)
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    return issued


def _me(client: TestClient, token: str | None, scheme: str = "Bearer"):
    headers = {"Authorization": f"{scheme} {token}"} if token else {}
    return client.get("/api/me", headers=headers)


@pytest.mark.parametrize(("pid", "kind"), [("stephane", "human"), ("leader", "agent")])
def test_token_learns_its_player_and_kind(client: TestClient, tokens: dict, pid, kind) -> None:
    r = _me(client, tokens[pid])
    assert r.status_code == 200
    assert r.json() == {"player": pid, "kind": kind}
    assert r.headers["cache-control"] == "no-store"


def test_no_game_needed(client: TestClient, tokens: dict, games_dir: Path) -> None:
    assert not list(games_dir.glob("*.pgn"))
    assert _me(client, tokens["stephane"]).status_code == 200


@pytest.mark.parametrize("token", [None, "pas-un-jeton"], ids=["absent", "malformé"])
def test_missing_or_malformed_token_is_401(client: TestClient, tokens: dict, token) -> None:
    r = _me(client, token)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_well_formed_but_unknown_token_is_401(client: TestClient, tokens: dict) -> None:
    _id, secret = token_format.parse_token(tokens["stephane"])
    assert _me(client, token_format.format_token("00000000", secret)).status_code == 401
    assert _me(client, token_format.format_token(_id, secret[::-1])).status_code == 401


def test_other_scheme_is_401(client: TestClient, tokens: dict) -> None:
    assert _me(client, tokens["stephane"], scheme="Basic").status_code == 401


def test_revoked_token_is_401(client: TestClient, tokens: dict, games_dir: Path) -> None:
    token_id, _secret = token_format.parse_token(tokens["leader"])
    registry.revoke_token(games_dir, "leader", token_id)
    assert _me(client, tokens["leader"]).status_code == 401


def test_unreadable_registry_is_503_never_a_pass(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    registry.registry_path(games_dir).write_text("{not json", encoding="utf-8")
    r = _me(client, tokens["stephane"])
    assert r.status_code == 503
    assert "stephane" not in r.text


def test_token_never_in_the_answer(client: TestClient, tokens: dict) -> None:
    for token in (tokens["stephane"], "pas-un-jeton"):
        assert token not in _me(client, token).text
