"""Open sign-up (#351, PR 3) : ``POST /api/players``.

Players named by function. The client speaks HTTPS, as the browser does
behind the reverse proxy : a ``Secure`` cookie is only sent back over it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.chessboard_viewer import build_app
from palamede_chessboard.player_prefs import PREFS_ENV
from palamede_chessboard.players import registry
from palamede_chessboard.viewer import session
from palamede_chessboard.viewer.routers import players

NEWCOMER, PAST, MEMBER, SPECTATOR = "newcomer", "past-player", "relay-member", "onlooker"
ORIGIN = {"Origin": "https://testserver"}


@pytest.fixture
def games_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    for name in (registry.REGISTRY_ENV, session.INSECURE_ENV, PREFS_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(players, "SESSION_TRIES", session.RateLimit(10, 60))
    monkeypatch.setattr(players, "SIGNUPS_PER_ADDRESS", session.RateLimit(5, 3600))
    monkeypatch.setattr(players, "SIGNUPS_IN_ALL", session.RateLimit(30, 3600))
    return tmp_path


@pytest.fixture
def browser(games_dir: Path) -> TestClient:
    return TestClient(build_app(), base_url="https://testserver")


def _sign_up(browser: TestClient, name: str, *, recovery: bool = True, headers=None):
    body = {"player_id": name, "display_name": name, "recovery": recovery}
    return browser.post("/api/players", json=body, headers=ORIGIN if headers is None else headers)


def _game(games_dir: Path, game_id: str, white: str, black: str, **extra) -> None:
    metadata = GameMetadata(
        game_id=game_id,
        white=white,
        black=black,
        time_control=TimeControl(format="unrated"),
        **extra,
    )
    GameState.create(metadata, games_dir=games_dir).save()


def test_a_newcomer_signs_up_and_is_signed_in(browser: TestClient, games_dir: Path) -> None:
    r = _sign_up(browser, NEWCOMER)
    assert r.status_code == 201
    body = r.json()
    assert body["player"] == NEWCOMER and body["kind"] == "human"
    cookie = r.headers.get("set-cookie", "").lower()
    for flag in ("httponly", "secure", "samesite=lax", f"max-age={session.COOKIE_MAX_AGE}"):
        assert flag in cookie
    assert r.headers["cache-control"] == "no-store"
    assert browser.get("/api/me").json() == {"player": NEWCOMER, "kind": "human"}
    listed = {t["class"] for t in browser.get("/api/me/tokens").json()["tokens"]}
    assert listed == {"browser", "recovery"}


def test_the_recovery_token_is_shown_once_and_only_opens_a_session(
    browser: TestClient, games_dir: Path
) -> None:
    recovery = _sign_up(browser, NEWCOMER).json()["recovery"]
    assert recovery.startswith("plm_")
    stored = json.loads(registry.registry_path(games_dir).read_text())
    assert recovery not in json.dumps(stored)  # only its digest is kept
    bearer = {"Authorization": f"Bearer {recovery}"}
    assert TestClient(build_app()).get("/api/me", headers=bearer).status_code == 401
    other = TestClient(build_app(), base_url="https://testserver")
    r = other.post("/api/session", json={"token": recovery}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["player"] == NEWCOMER


def test_the_recovery_token_can_be_refused(browser: TestClient, games_dir: Path) -> None:
    r = _sign_up(browser, NEWCOMER, recovery=False)
    assert r.status_code == 201 and r.json()["recovery"] is None
    assert [t["class"] for t in registry.tokens_of(games_dir, NEWCOMER)] == ["browser"]


@pytest.mark.parametrize(
    "asked",
    [PAST, "Past-Player", " PAST-PLAYER ", MEMBER, SPECTATOR, "the-arbiter", "the-annotator"],
)
def test_a_name_of_the_games_is_unavailable(
    browser: TestClient, games_dir: Path, asked: str
) -> None:
    _game(
        games_dir,
        "g1",
        "Past-Player",
        "someone",
        white_team=["Teammate", "Relay-Member"],
        spectators=["Watcher", "Onlooker"],
        arbiter="The-Arbiter",
        annotator="The-Annotator",
    )
    r = _sign_up(browser, asked)
    assert r.status_code == 409 and r.json()["detail"] == "Nom indisponible"
    assert "set-cookie" not in r.headers
    assert registry.load(games_dir)["players"] == {}


def test_every_taken_name_gets_the_same_answer(
    browser: TestClient, games_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(players, "SIGNUPS_PER_ADDRESS", session.RateLimit(30, 3600))
    registry.add_player(games_dir, "account-holder", kind="human")
    (games_dir / ".player_prefs.json").write_text(json.dumps({"players": {"pref-only": {}}}))
    _game(games_dir, "g1", PAST, "someone")
    answers = {
        name: (r.status_code, r.json()["detail"])
        for name in ("account-holder", "pref-only", PAST, "leader", "devleader", "Admin")
        for r in [_sign_up(browser, name)]
    }
    assert set(answers.values()) == {(409, "Nom indisponible")}
    assert set(registry.load(games_dir)["players"]) == {"account-holder"}


def test_an_invalid_name_is_refused_with_the_rule(
    browser: TestClient, games_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(players, "SIGNUPS_PER_ADDRESS", session.RateLimit(30, 3600))
    for name in ("", "a b", "x" * 33, "élève", "-dash"):
        r = _sign_up(browser, name)
        assert r.status_code in (400, 422), name
    assert _sign_up(browser, "a b").json()["detail"].startswith("Nom invalide")
    assert registry.load(games_dir)["players"] == {}


def test_the_display_name_is_cleaned_and_bounded(browser: TestClient, games_dir: Path) -> None:
    body = {"player_id": NEWCOMER, "display_name": "New\x00\ncomer\x1b"}
    assert browser.post("/api/players", json=body, headers=ORIGIN).status_code == 201
    assert registry.load(games_dir)["players"][NEWCOMER]["display_name"] == "Newcomer"
    long = {"player_id": "other", "display_name": "y" * 65}
    assert browser.post("/api/players", json=long, headers=ORIGIN).status_code == 400


def test_only_the_viewer_s_page_signs_up(browser: TestClient, games_dir: Path) -> None:
    for headers in ({}, {"Origin": "https://evil.example"}, {"Origin": "null"}):
        r = _sign_up(browser, NEWCOMER, headers=headers)
        assert r.status_code == 403 and "set-cookie" not in r.headers
    assert registry.load(games_dir)["players"] == {}


def test_five_sign_ups_an_hour_per_address_then_429(browser: TestClient, games_dir: Path) -> None:
    for n in range(5):
        assert _sign_up(browser, f"player-{n}").status_code == 201
    r = _sign_up(browser, "player-5")
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert "player-5" not in registry.load(games_dir)["players"]


def test_refused_tries_count_too(browser: TestClient, games_dir: Path) -> None:
    _game(games_dir, "g1", PAST, "someone")
    for _ in range(5):
        assert _sign_up(browser, PAST).status_code == 409
    assert _sign_up(browser, NEWCOMER).status_code == 429


def test_thirty_an_hour_in_all(
    browser: TestClient, games_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(players, "SIGNUPS_IN_ALL", session.RateLimit(2, 3600))
    assert _sign_up(browser, "player-a").status_code == 201
    assert _sign_up(browser, "player-b").status_code == 201
    assert _sign_up(browser, "player-c").status_code == 429


def test_an_unreadable_registry_refuses(browser: TestClient, games_dir: Path) -> None:
    path = registry.registry_path(games_dir)
    path.parent.mkdir()
    path.write_text("{not json")
    assert _sign_up(browser, NEWCOMER).status_code == 503


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_game_refuses_rather_than_let_its_names_through(
    browser: TestClient, games_dir: Path
) -> None:
    _game(games_dir, "g1", PAST, "someone")
    pgn = games_dir / "g1.pgn"
    pgn.chmod(0)
    try:
        r = _sign_up(browser, NEWCOMER)
    finally:
        pgn.chmod(0o600)
    assert r.status_code == 503
    assert registry.load(games_dir)["players"] == {}


def test_a_new_account_cannot_act_in_another_s_game(browser: TestClient, games_dir: Path) -> None:
    _game(games_dir, "g1", "white-player", "black-player")
    assert _sign_up(browser, NEWCOMER).status_code == 201
    assert browser.post("/api/games/g1/resign", headers=ORIGIN).status_code == 403


def test_a_signed_in_player_gets_a_recovery_token_once(
    browser: TestClient, games_dir: Path
) -> None:
    """#351 : the page signs up without it, then offers it once the account exists."""
    assert _sign_up(browser, NEWCOMER, recovery=False).status_code == 201
    r = browser.post("/api/me/recovery", headers=ORIGIN)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    spare = r.json()["recovery"]
    assert r.json()["player"] == NEWCOMER
    other = TestClient(build_app(), base_url="https://testserver")
    assert other.post("/api/session", json={"token": spare}, headers=ORIGIN).status_code == 200


def test_a_new_recovery_token_revokes_the_previous_one(
    browser: TestClient, games_dir: Path
) -> None:
    first = _sign_up(browser, NEWCOMER).json()["recovery"]
    second = browser.post("/api/me/recovery", headers=ORIGIN).json()["recovery"]
    assert first != second
    listed = [t for t in registry.tokens_of(games_dir, NEWCOMER) if t["class"] == "recovery"]
    assert [t["revoked"] is None for t in listed] == [False, True]
    fresh = TestClient(build_app(), base_url="https://testserver")
    assert fresh.post("/api/session", json={"token": first}, headers=ORIGIN).status_code == 401
    assert fresh.post("/api/session", json={"token": second}, headers=ORIGIN).status_code == 200


@pytest.mark.parametrize("kind", ["device", "recovery", "guest"])
def test_only_api_and_browser_tokens_ask_a_recovery_token(games_dir: Path, kind: str) -> None:
    registry.add_player(games_dir, NEWCOMER, kind="human")
    token = registry.issue_token(games_dir, NEWCOMER, label="t", token_class=kind)
    bearer = {"Authorization": f"Bearer {token}"}
    assert TestClient(build_app()).post("/api/me/recovery", headers=bearer).status_code == 401
    assert registry.tokens_of(games_dir, NEWCOMER)[1:] == []


def test_an_api_token_asks_one_too(games_dir: Path) -> None:
    registry.add_player(games_dir, NEWCOMER, kind="human")
    api = registry.issue_token(games_dir, NEWCOMER, label="cli")
    r = TestClient(build_app()).post("/api/me/recovery", headers={"Authorization": f"Bearer {api}"})
    assert r.status_code == 200 and r.json()["recovery"].startswith("plm_")


def test_nobody_signed_in_gets_no_recovery_token(browser: TestClient, games_dir: Path) -> None:
    assert browser.post("/api/me/recovery", headers=ORIGIN).status_code == 401


def test_a_recovery_token_is_not_asked_from_another_site(
    browser: TestClient, games_dir: Path
) -> None:
    assert _sign_up(browser, NEWCOMER, recovery=False).status_code == 201
    evil = {"Origin": "https://evil.example"}
    assert browser.post("/api/me/recovery", headers=evil).status_code == 403
    assert [t["class"] for t in registry.tokens_of(games_dir, NEWCOMER)] == ["browser"]


def test_the_page_asks_the_name_before_the_recovery_token() -> None:
    """Smoke of the owner, 16/11 : a taken name must be refused before any question."""
    script = (
        Path(__file__).parents[2] / "palamede_chessboard/templates/viewer/_state.js.jinja"
    ).read_text(encoding="utf-8")
    body = script[script.index("async function signUp()") :]
    body = body[: body.index("\n}\n")]
    assert "recovery: false" in body
    assert (
        body.index("'/api/players'")
        < body.index("RECOVERY_OFFER")
        < body.index("'/api/me/recovery'")
    )
    assert body.index("if (!created.ok)") < body.index("RECOVERY_OFFER")
