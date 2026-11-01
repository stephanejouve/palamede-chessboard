"""The Tablier's half of the relay hold (#164) : say the mode, say when the board matches."""

from __future__ import annotations

import time

import chess
import pytest
from palamede_chessnut import clock_hold, viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.clock import seconds_left
from palamede_chessnut.games import Session

from tests.test_chessnut import Board, after, frame, placement, ready_bridge, snapshot


@pytest.fixture
def posts(monkeypatch) -> list[tuple[str, str, dict]]:
    sent: list[tuple[str, str, dict]] = []

    def post(url: str, token: str, payload: dict | None = None) -> tuple[int, str]:
        sent.append((url, token, payload or {}))
        return 200, ""

    monkeypatch.setattr(viewer, "post", post)
    return sent


def test_the_bridge_declares_its_mode(posts) -> None:
    bridge = Bridge("https://viewer.test", "tablier.s.1", chess.WHITE, "tok", with_board=False)
    assert clock_hold.declare(bridge) is True
    assert posts == [("https://viewer.test/api/games/tablier.s.1/board", "tok", {"board": False})]


def test_an_older_viewer_holds_nothing(monkeypatch) -> None:
    monkeypatch.setattr(viewer, "post", lambda *_a, **_k: (404, "Not Found"))
    assert clock_hold.declare(ready_bridge()) is False


async def test_the_board_matching_on_my_turn_is_said_once(posts) -> None:
    """The opponent's 1...e5 set on the board : said once, with the placement."""
    bridge = ready_bridge(after("e4"))
    bridge.apply_stream(snapshot(after("e4", "e5")))
    board = Board()
    bridge.on_frame(None, bytearray(frame(placement(after("e4", "e5")))))
    bridge.settled_since = time.monotonic() - 1.0
    await bridge.tick(board.write)
    await bridge.tick(board.write)
    for _ in range(50):
        if posts:
            break
        time.sleep(0.01)
    assert posts == [
        (
            "https://viewer.test/api/games/g1/synced",
            "t",
            {"fen": after("e4", "e5").board_fen()},
        )
    ]


async def test_nothing_is_said_on_the_opponents_turn(posts) -> None:
    """My own 1.e4 confirmed by the stream : the opponent's time is not mine to release."""
    bridge = ready_bridge()
    bridge.apply_stream(snapshot(after("e4")))
    bridge.on_frame(None, bytearray(frame(placement(after("e4")))))
    bridge.settled_since = time.monotonic() - 1.0
    await bridge.tick(Board().write)
    time.sleep(0.05)
    assert posts == []


def test_a_held_clock_shows_no_extra_time() -> None:
    """During the hold, last_move_ts is ahead of now : the time stays, it never grows."""
    clock = {
        "white_seconds": 303.0,
        "black_seconds": 296.0,
        "now_ts": 1000.0,
        "last_move_ts": 1015.0,
        "active": "white",
        "started": True,
        "flag_fall": None,
    }
    assert seconds_left(clock)["white"] == 303.0
    assert seconds_left(clock, age=20.0)["white"] == 298.0


def test_closing_the_window_leaves_the_board(posts) -> None:
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, "t", with_board=True)
    session = Session(bridge, lambda b: b.stopped.wait(5))
    session.start()
    session.close()
    assert posts[-1] == ("https://viewer.test/api/games/g1/board", "t", {"board": False})


def test_closing_a_mouse_window_says_nothing(posts) -> None:
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, "t", with_board=False)
    session = Session(bridge, lambda b: b.stopped.wait(5))
    session.start()
    session.close()
    assert posts == []
