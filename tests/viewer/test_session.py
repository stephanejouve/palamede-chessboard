"""The browser's session (#351, PR 2) : the token in an HttpOnly cookie.

Players named by function. The client speaks HTTPS, as the browser does
behind the reverse proxy : a ``Secure`` cookie is only sent back over it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.chessboard_viewer import build_app
from palamede_chessboard.players import registry
from palamede_chessboard.viewer import session
from palamede_chessboard.viewer.routers import players

HUMAN, OTHER = "human-player", "other-player"
ORIGIN = {"Origin": "https://testserver"}


@pytest.fixture
def games_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    monkeypatch.delenv(session.INSECURE_ENV, raising=False)
    monkeypatch.setattr(players, "SESSION_TRIES", session.RateLimit(10, 60))
    return tmp_path


@pytest.fixture
def browser(games_dir: Path) -> TestClient:
    return TestClient(build_app(), base_url="https://testserver")


@pytest.fixture
def tokens(games_dir: Path) -> dict[str, str]:
    registry.add_player(games_dir, HUMAN, kind="human")
    registry.add_player(games_dir, OTHER, kind="human")
    return {
        "api": registry.issue_token(games_dir, HUMAN, label="cli"),
        "recovery": registry.issue_token(games_dir, HUMAN, label="spare", token_class="recovery"),
        "guest": registry.issue_token(games_dir, HUMAN, label="game", token_class="guest"),
        "other": registry.issue_token(games_dir, OTHER, label="cli"),
    }


def _sign_in(browser: TestClient, token: str, headers: dict | None = None):
    return browser.post("/api/session", json={"token": token}, headers=ORIGIN | (headers or {}))


def _set_cookie(response) -> str:
    return response.headers.get("set-cookie", "")


def _game(games_dir: Path) -> str:
    metadata = GameMetadata(
        game_id="g1", white=HUMAN, black=OTHER, time_control=TimeControl(format="unrated")
    )
    GameState.create(metadata, games_dir=games_dir).save()
    return "g1"


def test_the_pasted_token_opens_a_browser_session(
    browser: TestClient, tokens: dict, games_dir: Path
) -> None:
    r = _sign_in(browser, tokens["api"])
    assert r.status_code == 200 and r.json() == {"player": HUMAN, "kind": "human"}
    cookie = _set_cookie(r)
    for flag in (
        "HttpOnly",
        "Secure",
        "SameSite=lax",
        "Path=/",
        f"Max-Age={session.COOKIE_MAX_AGE}",
    ):
        assert flag.lower() in cookie.lower()
    assert tokens["api"] not in cookie  # a new token, never the pasted one
    listed = registry.tokens_of(games_dir, HUMAN)
    assert [t["class"] for t in listed].count("browser") == 1
    # The cookie alone now says who the browser is.
    assert browser.get("/api/me").json() == {"player": HUMAN, "kind": "human"}


def test_the_recovery_token_opens_a_session_and_nothing_else(
    browser: TestClient, tokens: dict
) -> None:
    # Owner, 14/11 : its only use.
    assert (
        browser.get(
            "/api/me", headers={"Authorization": f"Bearer {tokens['recovery']}"}
        ).status_code
        == 401
    )
    assert _sign_in(browser, tokens["recovery"]).status_code == 200
    assert browser.get("/api/me").json()["player"] == HUMAN


@pytest.mark.parametrize("which", ["guest", None])
def test_a_guest_or_a_made_up_token_opens_nothing(
    browser: TestClient, tokens: dict, which: str | None
) -> None:
    token = tokens[which] if which else "plm_00000000_" + "a" * 43
    r = _sign_in(browser, token)
    assert r.status_code == 401
    assert session.COOKIE_NAME not in _set_cookie(r)


def test_only_the_viewer_s_page_signs_in_or_out(browser: TestClient, tokens: dict) -> None:
    for origin in ({}, {"Origin": "https://evil.example"}, {"Origin": "null"}):
        r = browser.post("/api/session", json={"token": tokens["api"]}, headers=origin)
        assert r.status_code == 403 and session.COOKIE_NAME not in _set_cookie(r)
        assert browser.post("/api/session/logout", headers=origin).status_code == 403
    # Behind the reverse proxy : the forwarded host is the one the browser asked.
    proxied = {"Origin": "https://chess.example", "X-Forwarded-Host": "chess.example"}
    assert (
        browser.post("/api/session", json={"token": tokens["api"]}, headers=proxied).status_code
        == 200
    )


def test_signing_out_expires_the_cookie_the_token_stays_valid(
    browser: TestClient, tokens: dict, games_dir: Path
) -> None:
    _sign_in(browser, tokens["api"])
    browser_token = next(t for t in registry.tokens_of(games_dir, HUMAN) if t["class"] == "browser")
    r = browser.post("/api/session/logout", headers=ORIGIN)
    assert r.status_code == 200 and r.json() == {"signed_out": True}
    assert "max-age=0" in _set_cookie(r).lower()
    assert browser.get("/api/me").status_code == 401
    # Owner, 14/11 : signing out revokes nothing.
    still = next(t for t in registry.tokens_of(games_dir, HUMAN) if t["id"] == browser_token["id"])
    assert still["revoked"] is None


def test_the_bearer_wins_and_an_invalid_one_never_falls_back(
    browser: TestClient, tokens: dict
) -> None:
    _sign_in(browser, tokens["api"])
    other = browser.get("/api/me", headers={"Authorization": f"Bearer {tokens['other']}"})
    assert other.json()["player"] == OTHER
    # Review Admin #351, point 4 : a wrong Bearer is a 401, the cookie not used.
    wrong = browser.get("/api/me", headers={"Authorization": "Bearer plm_00000000_" + "b" * 43})
    assert wrong.status_code == 401


def test_a_cookie_write_from_another_site_is_refused_before_the_route(
    browser: TestClient, tokens: dict, games_dir: Path
) -> None:
    game = _game(games_dir)
    _sign_in(browser, tokens["api"])
    for origin in ({}, {"Origin": "https://evil.example"}):
        r = browser.post(f"/api/games/{game}/resign", headers=origin)
        assert r.status_code == 403 and r.json()["detail"] == session.CROSS_SITE
    assert GameState.load(game, games_dir=games_dir).metadata.result == "*"
    # From the page, the same cookie plays : the route runs.
    assert browser.post(f"/api/games/{game}/resign", headers=ORIGIN).status_code == 200


def test_a_bearer_write_needs_no_origin(browser: TestClient, tokens: dict, games_dir: Path) -> None:
    # Agents, the Tablier and scripts send their token : no cookie, no CSRF.
    game = _game(games_dir)
    r = browser.post(
        f"/api/games/{game}/resign", headers={"Authorization": f"Bearer {tokens['api']}"}
    )
    assert r.status_code == 200


def test_a_bearer_write_from_a_signed_in_browser_is_the_bearer_s(
    browser: TestClient, tokens: dict, games_dir: Path
) -> None:
    # Review Admin #351, point 4 : with both, the Bearer decides, and the
    # cookie's Origin check does not apply to a request that is not the cookie's.
    game = _game(games_dir)
    _sign_in(browser, tokens["api"])
    r = browser.post(
        f"/api/games/{game}/resign", headers={"Authorization": f"Bearer {tokens['api']}"}
    )
    assert r.status_code == 200


def test_a_malformed_cookie_is_ignored(browser: TestClient, tokens: dict, games_dir: Path) -> None:
    game = _game(games_dir)
    # Sent as the browser would, whatever the client's cookie jar does.
    junk = {"Cookie": f"{session.COOKIE_NAME}=not-a-token"}
    assert browser.get("/api/me", headers=junk).status_code == 401
    # Not a session : no Origin check, the route answers as without a cookie.
    assert browser.post(f"/api/games/{game}/resign", headers=junk).status_code == 401
    # The same request with a real session cookie, from no page, is refused before the route.
    _sign_in(browser, tokens["api"])
    assert browser.post(f"/api/games/{game}/resign").status_code == 403


def test_revoking_the_session_s_own_token_expires_its_cookie(
    browser: TestClient, tokens: dict, games_dir: Path
) -> None:
    _sign_in(browser, tokens["api"])
    mine = next(t for t in browser.get("/api/me/tokens").json()["tokens"] if t["current"])
    r = browser.delete(f"/api/me/tokens/{mine['id']}", headers=ORIGIN)
    assert r.status_code == 200 and "max-age=0" in _set_cookie(r).lower()
    assert browser.get("/api/me").status_code == 401


def test_revoking_another_token_keeps_the_cookie(browser: TestClient, tokens: dict) -> None:
    _sign_in(browser, tokens["api"])
    cli = next(t for t in browser.get("/api/me/tokens").json()["tokens"] if t["label"] == "cli")
    r = browser.delete(f"/api/me/tokens/{cli['id']}", headers=ORIGIN)
    assert r.status_code == 200 and session.COOKIE_NAME not in _set_cookie(r)
    assert browser.get("/api/me").status_code == 200


def test_ten_tries_a_minute_then_429(browser: TestClient, tokens: dict) -> None:
    for _ in range(10):
        assert _sign_in(browser, "plm_00000000_" + "c" * 43).status_code == 401
    r = _sign_in(browser, tokens["api"])
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_the_insecure_setting_drops_secure_only(
    browser: TestClient, tokens: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(session.INSECURE_ENV, "1")
    cookie = _set_cookie(_sign_in(browser, tokens["api"])).lower()
    assert "secure" not in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_the_rate_limit_forgets_past_windows() -> None:
    limit = session.RateLimit(2, 60)
    assert limit.retry_after("a", now=0) is None and limit.retry_after("a", now=1) is None
    assert limit.retry_after("a", now=2) == 59
    assert limit.retry_after("a", now=61) is None  # the first call left the window
    assert limit.retry_after("b", now=2) is None  # per key
