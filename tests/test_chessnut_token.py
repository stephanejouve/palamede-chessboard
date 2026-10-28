"""The player token's light in the app (#78, step A) : checked at launch, never shown."""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import logging

import chess
import pytest
from palamede_chessnut import mouse, token_light, viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.status import RecentLog, snapshot

TOKEN = "plm_secret-token-never-shown"


def bridge_with_seat(status: int | None, body=None, player: chess.Color = chess.WHITE) -> Bridge:
    bridge = Bridge("https://viewer.test", "g1", player, TOKEN, with_board=False)
    bridge.seat = lambda: (status, body)  # type: ignore[method-assign]
    bridge.apply_stream(
        {"fen": chess.STARTING_FEN, "ply": 0, "turn": "white", "is_game_over": False}
    )
    return bridge


@pytest.mark.parametrize(
    ("status", "body", "light"),
    [
        (200, {"player": "stephane", "colors": ["white"]}, "on"),
        (200, {"player": "stephane", "colors": ["white", "black"]}, "on"),
        (200, {"player": "stephane", "colors": ["black"]}, "wrong_color"),
        (401, None, "refused"),
        (403, None, "not_player"),
        (404, None, "unchecked"),
        (None, None, "unchecked"),
        (500, None, "unchecked"),
    ],
)
def test_light_from_the_viewers_answer(status, body, light) -> None:
    bridge = bridge_with_seat(status, body)
    token_light.check(bridge)
    assert bridge.token_light == light


def test_account_name_reaches_the_page_not_the_token() -> None:
    bridge = bridge_with_seat(200, {"player": "stephane", "colors": ["white"]})
    token_light.check(bridge)
    state = snapshot(bridge, RecentLog())
    assert state["account"] == "stephane"
    assert state["lights"]["token"] == "on"
    assert state["token_message"] is None
    text = repr(state)
    assert TOKEN not in text
    assert hashlib.sha256(TOKEN.encode()).hexdigest()[:8] not in text


@pytest.mark.parametrize(
    ("status", "body", "words"),
    [
        (401, None, "relancez l'app"),
        (403, None, "ne joue pas cette partie"),
        (200, {"player": "stephane", "colors": ["black"]}, "Blancs"),
        (404, None, "non vérifié"),
    ],
)
def test_the_page_says_what_is_wrong(status, body, words) -> None:
    bridge = bridge_with_seat(status, body)
    token_light.check(bridge)
    assert words in snapshot(bridge)["token_message"]


@pytest.mark.parametrize(
    ("status", "body"),
    [(401, None), (403, None), (200, {"player": "stephane", "colors": ["black"]})],
)
def test_no_mouse_move_with_a_refused_token(status, body, monkeypatch) -> None:
    sent: list = []
    monkeypatch.setattr(viewer, "post", lambda *a, **k: sent.append(a) or (200, ""))
    bridge = bridge_with_seat(status, body)
    token_light.check(bridge)
    assert mouse.legal_targets(bridge) == ({}, [])
    code, message = mouse.play_from_page(bridge, "e2e4")
    assert code == 409 and "Jeton" in message
    assert sent == []


def test_no_mouse_move_before_the_check() -> None:
    bridge = bridge_with_seat(200, {"player": "stephane", "colors": ["white"]})
    assert bridge.token_light == "checking"
    assert mouse.legal_targets(bridge) == ({}, [])


def test_unchecked_token_still_plays(monkeypatch) -> None:
    """An older viewer has no /seat : the first move tells, as before."""
    monkeypatch.setattr(viewer, "post", lambda *a, **k: (200, ""))
    bridge = bridge_with_seat(404)
    token_light.check(bridge)
    assert mouse.play_from_page(bridge, "e2e4") == (200, "ok")


def test_a_401_on_a_move_turns_the_light_red(monkeypatch) -> None:
    monkeypatch.setattr(viewer, "post", lambda *a, **k: (401, "Jeton de joueur requis"))
    bridge = bridge_with_seat(200, {"player": "stephane", "colors": ["white"]})
    token_light.check(bridge)
    mouse.play_from_page(bridge, "e2e4")
    assert bridge.token_light == "refused"
    assert snapshot(bridge)["lights"]["token"] == "refused"


def test_fingerprint_is_logged_once_never_the_token(caplog) -> None:
    bridge = bridge_with_seat(200, {"player": "stephane", "colors": ["white"]})
    with caplog.at_level(logging.DEBUG):
        token_light.check(bridge)
    digest = hashlib.sha256(TOKEN.encode()).hexdigest()[:8]
    lines = [r.getMessage() for r in caplog.records if digest in r.getMessage()]
    assert lines == [f"player token : {len(TOKEN)} characters, sha256 {digest}, on"]
    assert TOKEN not in caplog.text and "Bearer" not in caplog.text
    # A fingerprint of the whole token, not a piece of it.
    assert digest not in TOKEN


def test_seat_sends_the_token_unredirected(monkeypatch) -> None:
    seen = {}

    class Answer:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *_args):
            return b'{"player": "stephane", "colors": ["white"]}'

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["redirected"] = request.headers.get("Authorization")
        seen["auth"] = request.unredirected_hdrs.get("Authorization")
        return Answer()

    monkeypatch.setattr(viewer._OPENER, "open", opener)
    bridge = Bridge("https://viewer.test", "g 1", chess.WHITE, TOKEN, with_board=False)
    status, body = bridge.seat()
    assert status == 200 and body["player"] == "stephane"
    assert seen["url"] == "https://viewer.test/api/games/g%201/seat"
    assert seen["auth"] == f"Bearer {TOKEN}" and seen["redirected"] is None


@pytest.mark.parametrize(
    "error",
    [http.client.BadStatusLine("garbage"), http.client.IncompleteRead(b"{")],
    ids=["bad status line", "incomplete read"],
)
def test_a_malformed_answer_leaves_the_light_orange(monkeypatch, error) -> None:
    """Review Junior #106 : no exception out of the check, the bridge starts."""

    def opener(request, timeout):
        raise error

    monkeypatch.setattr(viewer._OPENER, "open", opener)
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN, with_board=False)
    assert bridge.seat() == (None, None)
    token_light.check(bridge)
    assert bridge.token_light == "unchecked"


def test_page_move_waits_for_the_check(monkeypatch) -> None:
    sent: list = []
    monkeypatch.setattr(viewer, "post", lambda *a, **k: sent.append(a) or (200, ""))
    bridge = bridge_with_seat(200, {"player": "stephane", "colors": ["white"]})
    code, message = mouse.play_from_page(bridge, "e2e4")
    assert code == 409 and "Jeton" in message
    assert sent == []


@pytest.mark.parametrize(
    ("status", "body"),
    [(403, None), (200, {"player": "stephane", "colors": ["black"]})],
    ids=["not a player", "wrong colour"],
)
def test_every_refusal_shows_red(status, body) -> None:
    bridge = bridge_with_seat(status, body)
    token_light.check(bridge)
    assert snapshot(bridge)["lights"]["token"] == "refused"


def test_run_checks_the_token_before_watching(monkeypatch) -> None:
    order = []
    bridge = bridge_with_seat(200, {"player": "stephane", "colors": ["white"]})
    monkeypatch.setattr(token_light, "check", lambda b: order.append("check"))
    monkeypatch.setattr(bridge, "_follow_stream", lambda loop: None)

    async def watch() -> None:
        order.append("watch")

    monkeypatch.setattr(bridge, "watch_game", watch)
    asyncio.run(bridge.run())
    assert order == ["check", "watch"]
