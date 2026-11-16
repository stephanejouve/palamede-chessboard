"""Pairing a board to a player (#351 §4, #227, RFC 8628).

Players named by function. The browser client speaks HTTPS, as behind the
reverse proxy ; the board is a separate client without any cookie.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_viewer import build_app
from palamede_chessboard.players import pairing, registry
from palamede_chessboard.viewer import session
from palamede_chessboard.viewer.routers import devices, players

OWNER, OTHER = "board-owner", "other-player"
ORIGIN = {"Origin": "https://testserver"}


@pytest.fixture
def games_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    for name in (registry.REGISTRY_ENV, session.INSECURE_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(players, "SESSION_TRIES", session.RateLimit(10, 60))
    monkeypatch.setattr(devices, "PAIRINGS", pairing.Pairings())
    monkeypatch.setattr(devices, "CODE_REQUESTS", session.RateLimit(10, 60))
    monkeypatch.setattr(devices, "WRONG_CODES", session.RateLimit(5, 60))
    return tmp_path


@pytest.fixture
def tokens(games_dir: Path) -> dict[str, str]:
    registry.add_player(games_dir, OWNER, kind="human")
    registry.add_player(games_dir, OTHER, kind="human")
    return {
        "api": registry.issue_token(games_dir, OWNER, label="cli"),
        "browser": registry.issue_token(games_dir, OWNER, label="nav", token_class="browser"),
        "device": registry.issue_token(games_dir, OWNER, label="old", token_class="device"),
        "recovery": registry.issue_token(games_dir, OWNER, label="r", token_class="recovery"),
        "other": registry.issue_token(games_dir, OTHER, label="cli"),
    }


@pytest.fixture
def board(games_dir: Path) -> TestClient:
    return TestClient(build_app())


@pytest.fixture
def player(games_dir: Path, tokens: dict) -> TestClient:
    """The owner's browser, signed in with its cookie."""
    client = TestClient(build_app(), base_url="https://testserver")
    r = client.post("/api/session", json={"token": tokens["api"]}, headers=ORIGIN)
    assert r.status_code == 200
    return client


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The monotonic clock of the pairings, moved by hand."""
    now = [1000.0]
    monkeypatch.setattr(pairing.time, "monotonic", lambda: now[0])
    return now


def _ask(board: TestClient, name: str = "Chessnut Evo") -> dict:
    r = board.post("/api/device/code", json={"device_name": name})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    return r.json()


def _poll(board: TestClient, device_code: str):
    return board.post("/api/device/token", json={"device_code": device_code})


def test_the_whole_pairing(board: TestClient, player: TestClient, clock: list, games_dir) -> None:
    asked = _ask(board)
    assert asked["verification_uri"] == "/appairer"
    assert asked["expires_in"] == 600 and asked["interval"] == 5
    assert len(asked["user_code"]) == 9 and asked["user_code"][4] == "-"
    assert not set(asked["user_code"].replace("-", "")) & set("01OI")

    r = _poll(board, asked["device_code"])
    assert r.status_code == 400 and r.json() == {"error": "authorization_pending"}

    seen = player.post("/api/device/lookup", json={"user_code": asked["user_code"]}, headers=ORIGIN)
    assert seen.status_code == 200 and seen.json()["device_name"] == "Chessnut Evo"
    assert seen.json()["requested_at"]
    yes = player.post(
        "/api/device/approve",
        json={"user_code": asked["user_code"], "accept": True},
        headers=ORIGIN,
    )
    assert yes.json() == {"device_name": "Chessnut Evo", "paired": True}

    clock[0] += 5
    r = _poll(board, asked["device_code"])
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["token_type"] == "Bearer" and body["player"] == OWNER
    me = board.get("/api/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["player"] == OWNER
    listed = {t["label"]: t["class"] for t in registry.tokens_of(games_dir, OWNER)}
    assert listed["échiquier Chessnut Evo"] == "device"

    clock[0] += 5
    assert _poll(board, asked["device_code"]).json() == {"error": "expired_token"}  # once


def test_the_code_is_typed_loosely(board: TestClient, player: TestClient) -> None:
    code = _ask(board)["user_code"]
    loose = " " + code.lower().replace("-", " ") + " "
    r = player.post("/api/device/lookup", json={"user_code": loose}, headers=ORIGIN)
    assert r.status_code == 200


def test_polling_too_fast_slows_the_board_down(
    board: TestClient, clock: list, player: TestClient
) -> None:
    asked = _ask(board)
    assert _poll(board, asked["device_code"]).json()["error"] == "authorization_pending"
    clock[0] += 4
    assert _poll(board, asked["device_code"]).json()["error"] == "slow_down"
    clock[0] += 9  # the interval grew to 10 s
    assert _poll(board, asked["device_code"]).json()["error"] == "slow_down"
    clock[0] += 15
    assert _poll(board, asked["device_code"]).json()["error"] == "authorization_pending"


def test_a_refusal_is_said_once_then_the_code_is_dead(
    board: TestClient, player: TestClient, clock: list
) -> None:
    asked = _ask(board)
    no = player.post(
        "/api/device/approve",
        json={"user_code": asked["user_code"], "accept": False},
        headers=ORIGIN,
    )
    assert no.json()["paired"] is False
    assert _poll(board, asked["device_code"]).json() == {"error": "access_denied"}
    clock[0] += 5
    assert _poll(board, asked["device_code"]).json() == {"error": "expired_token"}


def test_a_code_serves_once(board: TestClient, player: TestClient, tokens: dict) -> None:
    code = _ask(board)["user_code"]
    body = {"user_code": code, "accept": True}
    assert player.post("/api/device/approve", json=body, headers=ORIGIN).status_code == 200
    again = player.post("/api/device/approve", json=body, headers=ORIGIN)
    assert again.status_code == 404
    other = {"Authorization": f"Bearer {tokens['other']}"}
    assert (
        TestClient(build_app()).post("/api/device/approve", json=body, headers=other).status_code
        == 404
    )


def test_an_expired_code_pairs_nothing(board: TestClient, player: TestClient, clock: list) -> None:
    asked = _ask(board)
    clock[0] += pairing.EXPIRES_IN
    r = player.post("/api/device/lookup", json={"user_code": asked["user_code"]}, headers=ORIGIN)
    assert r.status_code == 404 and r.json()["detail"] == devices.UNKNOWN_CODE
    assert _poll(board, asked["device_code"]).json() == {"error": "expired_token"}


def test_an_unknown_device_code_is_expired(board: TestClient) -> None:
    assert _poll(board, "made-up").json() == {"error": "expired_token"}


@pytest.mark.parametrize("kind", ["device", "recovery"])
def test_only_api_and_browser_tokens_pair_a_board(
    board: TestClient, tokens: dict, kind: str
) -> None:
    code = _ask(board)["user_code"]
    bearer = {"Authorization": f"Bearer {tokens[kind]}"}
    client = TestClient(build_app())
    assert (
        client.post("/api/device/lookup", json={"user_code": code}, headers=bearer).status_code
        == 401
    )
    r = client.post("/api/device/approve", json={"user_code": code, "accept": True}, headers=bearer)
    assert r.status_code == 401


def test_an_api_token_pairs_too(board: TestClient, tokens: dict) -> None:
    code = _ask(board)["user_code"]
    bearer = {"Authorization": f"Bearer {tokens['api']}"}
    r = TestClient(build_app()).post(
        "/api/device/approve", json={"user_code": code, "accept": True}, headers=bearer
    )
    assert r.status_code == 200


def test_nobody_signed_in_pairs_nothing(board: TestClient, games_dir: Path) -> None:
    code = _ask(board)["user_code"]
    r = board.post("/api/device/approve", json={"user_code": code, "accept": True})
    assert r.status_code == 401


def test_a_cookie_answer_from_another_site_is_refused(
    board: TestClient, player: TestClient
) -> None:
    code = _ask(board)["user_code"]
    evil = {"Origin": "https://evil.example"}
    r = player.post("/api/device/approve", json={"user_code": code, "accept": True}, headers=evil)
    assert r.status_code == 403
    assert (
        player.post("/api/device/lookup", json={"user_code": code}, headers=ORIGIN).status_code
        == 200
    )


def test_five_wrong_codes_a_minute_then_429(
    board: TestClient, player: TestClient, clock: list
) -> None:
    code = _ask(board)["user_code"]
    for _ in range(5):
        r = player.post("/api/device/lookup", json={"user_code": "ZZZZ-ZZZZ"}, headers=ORIGIN)
        assert r.status_code == 404
    r = player.post("/api/device/lookup", json={"user_code": code}, headers=ORIGIN)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_right_codes_are_not_counted(board: TestClient, player: TestClient) -> None:
    for _ in range(6):
        code = _ask(board)["user_code"]
        r = player.post("/api/device/lookup", json={"user_code": code}, headers=ORIGIN)
        assert r.status_code == 200


def test_ten_codes_a_minute_per_address(board: TestClient) -> None:
    for _ in range(10):
        _ask(board)
    r = board.post("/api/device/code", json={})
    assert r.status_code == 429


def test_the_announced_name_is_cleaned_and_short(board: TestClient, player: TestClient) -> None:
    code = _ask(board, "Evil\x1b[31m\nboard" + "x" * 80)["user_code"]
    name = player.post("/api/device/lookup", json={"user_code": code}, headers=ORIGIN).json()
    assert name["device_name"].startswith("Evil[31mboard") and len(name["device_name"]) == 40
    unnamed = _ask(board, "  ")["user_code"]
    r = player.post("/api/device/lookup", json={"user_code": unnamed}, headers=ORIGIN)
    assert r.json()["device_name"] == "échiquier"


def test_the_pending_pairings_stay_bounded(
    board: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pairing, "MAX_PENDING", 2)
    _ask(board)
    _ask(board)
    assert board.post("/api/device/code", json={}).status_code == 503


def test_an_unreadable_registry_loses_the_yes_but_never_half_pairs(
    board: TestClient, player: TestClient, clock: list, games_dir: Path
) -> None:
    asked = _ask(board)
    body = {"user_code": asked["user_code"], "accept": True}
    assert player.post("/api/device/approve", json=body, headers=ORIGIN).status_code == 200
    registry.registry_path(games_dir).write_text("{broken")
    assert _poll(board, asked["device_code"]).status_code == 503
    clock[0] += 5
    assert _poll(board, asked["device_code"]).json() == {"error": "expired_token"}


def test_the_page_never_reads_the_code_from_its_address(board: TestClient) -> None:
    page = board.get("/appairer?code=ABCD-EFGH")
    assert page.status_code == 200
    assert "ABCD-EFGH" not in page.text
    assert "sur votre compte" in page.text and "vous venez de brancher" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    script = next(
        line.split('"')[1] for line in page.text.splitlines() if "/assets/appairer." in line
    )
    js = board.get(script).text
    assert "location" not in js and "URLSearchParams" not in js
