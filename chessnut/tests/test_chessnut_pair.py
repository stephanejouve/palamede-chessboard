"""Pairing the board by a short code (#351 §4, #227) : the Tablier's side.

The board's HTTP calls go to the real viewer app (FastAPI test client) :
the code, the player's yes on the page, the token received once, written
``0600``. Players named by function.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from palamede_chessnut import pair, viewer

from palamede_chessboard.chessboard_viewer import build_app
from palamede_chessboard.players import pairing, registry
from palamede_chessboard.viewer import session
from palamede_chessboard.viewer.routers import devices

VIEWER = "https://viewer.test"
OWNER = "board-owner"


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The viewer app, an owner with an api token, the board's calls routed to it."""
    games = tmp_path / "games"
    games.mkdir()
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(games))
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    monkeypatch.setattr(devices, "PAIRINGS", pairing.Pairings())
    monkeypatch.setattr(devices, "CODE_REQUESTS", session.RateLimit(10, 60))
    monkeypatch.setattr(devices, "POLLS", session.RateLimit(60, 60))
    now = [1000.0]  # the server's clock, moved by the board's waits
    monkeypatch.setattr(pairing.time, "monotonic", lambda: now[0])
    registry.add_player(games, OWNER, kind="human")
    api = registry.issue_token(games, OWNER, label="cli")
    client = TestClient(build_app())
    calls: list[str] = []

    def post(url: str, payload: dict[str, Any]) -> tuple[int | None, Any]:
        path = url.removeprefix(VIEWER)
        calls.append(path)
        r = client.post(path, json=payload)
        return r.status_code, r.json()

    monkeypatch.setattr(viewer, "post_json_without_token", post)
    return {"client": client, "api": api, "games": games, "calls": calls, "now": now}


def _answer(server: dict, code: str, accept: bool) -> None:
    bearer = {"Authorization": f"Bearer {server['api']}"}
    r = server["client"].post(
        "/api/device/approve", json={"user_code": code, "accept": accept}, headers=bearer
    )
    assert r.status_code == 200


def _pair(server: dict, token_file: Path, *, accept: bool = True, extra: list[str] = ()):
    """Run the command ; the player answers on the page during the first wait."""
    printed: list[str] = []
    answered: list[bool] = []

    def sleep(seconds: float) -> None:
        server["now"][0] += seconds  # the board waits its interval on the server's clock
        if not answered:
            code = next(line.strip() for line in printed if line.startswith("    "))
            _answer(server, code, accept)
            answered.append(True)

    original = pair.wait_token
    pair.wait_token = lambda url, asked: original(url, asked, sleep=sleep)  # type: ignore[assignment]
    try:
        pair.main(
            ["--viewer", VIEWER, "--token-file", str(token_file), "--name", "Evo", *extra],
            out=printed.append,
        )
    finally:
        pair.wait_token = original  # type: ignore[assignment]
    return printed


def test_the_board_is_paired_and_its_token_written_0600(server: dict, tmp_path: Path) -> None:
    token_file = tmp_path / "conf" / "token"
    printed = _pair(server, token_file)
    token = token_file.read_text(encoding="utf-8").strip()
    assert pair.TOKEN.fullmatch(token)
    me = server["client"].get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["player"] == OWNER
    listed = registry.tokens_of(server["games"], OWNER)
    assert {t["label"]: t["class"] for t in listed}["échiquier Evo"] == "device"
    assert all(token not in line for line in printed)  # never printed
    assert any("/appairer" in line for line in printed)
    assert any(line.startswith("Board paired to board-owner") for line in printed)
    if sys.platform != "win32":
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(token_file.parent.stat().st_mode) == 0o700
    assert [p for p in token_file.parent.iterdir() if p.name != "token"] == []  # no tmp left


def test_an_existing_token_file_is_kept_without_replace(
    server: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("plm_old\n", encoding="utf-8")

    def no_wait(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the board waited for a token over an existing file")

    with monkeypatch.context() as m:
        m.setattr(pair, "wait_token", no_wait)
        with pytest.raises(SystemExit, match="--replace"):
            pair.main(["--viewer", VIEWER, "--token-file", str(token_file)], out=lambda _l: None)
    assert token_file.read_text(encoding="utf-8") == "plm_old\n"
    assert server["calls"] == []  # nothing asked of the viewer
    _pair(server, token_file, extra=["--replace"])
    assert pair.TOKEN.fullmatch(token_file.read_text(encoding="utf-8").strip())


def test_a_refusal_writes_nothing(server: dict, tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    with pytest.raises(SystemExit, match="refused"):
        _pair(server, token_file, accept=False)
    assert not token_file.exists()


def test_an_expired_code_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([(400, {"error": "authorization_pending"})] * 3)
    monkeypatch.setattr(viewer, "post_json_without_token", lambda _u, _p: next(answers))
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    asked = {"device_code": "d", "expires_in": 12, "interval": 5}
    with pytest.raises(SystemExit, match="expired"):
        pair.wait_token(VIEWER, asked, sleep=sleep, clock=lambda: now[0])


def test_slow_down_and_429_lengthen_the_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(
        [
            (400, {"error": "slow_down"}),
            (429, {"detail": "Trop de relevés"}),
            (None, None),  # unreachable a moment : asked again
            (200, {"access_token": "plm_0123abcd_" + "x" * 43, "player": OWNER}),
        ]
    )
    monkeypatch.setattr(viewer, "post_json_without_token", lambda _u, _p: next(answers))
    waits: list[float] = []
    asked = {"device_code": "d", "expires_in": 600, "interval": 5}
    token, account = pair.wait_token(VIEWER, asked, sleep=waits.append, clock=lambda: 0.0)
    assert waits == [5, 10, 15, 15] and account == OWNER


def test_a_token_of_another_form_is_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        viewer, "post_json_without_token", lambda _u, _p: (200, {"access_token": "../etc"})
    )
    asked = {"device_code": "d", "expires_in": 600, "interval": 5}
    with pytest.raises(SystemExit, match="nothing written"):
        pair.wait_token(VIEWER, asked, sleep=lambda _s: None, clock=lambda: 0.0)


@pytest.mark.parametrize(
    ("status", "said"),
    [(None, "does not answer"), (404, "too old"), (429, "too many"), (500, "unexpected")],
)
def test_no_code_says_what_to_do(
    status: int | None, said: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(viewer, "post_json_without_token", lambda _u, _p: (status, None))
    with pytest.raises(SystemExit, match=said):
        pair.ask_code(VIEWER, "Evo")


def test_a_clear_url_is_refused_before_any_call(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(viewer, "post_json_without_token", lambda u, _p: calls.append(u))
    with pytest.raises(SystemExit, match="https required"):
        pair.main(
            ["--viewer", "http://chess.example", "--token-file", str(tmp_path / "t")],
            out=lambda _l: None,
        )
    assert calls == []


def test_a_failed_write_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_src: Any, _dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        pair.write_token(tmp_path / "token", "plm_0123abcd_" + "x" * 43)
    assert list(tmp_path.iterdir()) == []
