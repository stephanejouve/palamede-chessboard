"""The bridge's game server (#84, step 1) : the five points, behind one interface."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from typing import Any

import chess
import pytest
from palamede_chessnut import server as server_module
from palamede_chessnut import viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.server import ViewerServer

VIEWER = "https://viewer.test"


class FakeServer:
    """Another game server : records what the bridge asks."""

    def __init__(self, lines: list[bytes] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.lines = lines or []

    @contextlib.contextmanager
    def open_stream(self) -> Iterator[list[bytes]]:
        self.calls.append(("stream", None))
        yield self.lines

    def post_move(self, uci: str) -> tuple[int | None, str]:
        self.calls.append(("move", uci))
        return 200, ""

    def post_resign(self) -> tuple[int | None, str]:
        self.calls.append(("resign", None))
        return 200, ""

    def seat(self) -> tuple[int | None, Any]:
        self.calls.append(("seat", None))
        return 200, {"colors": ["black"]}

    def position(self) -> dict[str, Any] | None:
        self.calls.append(("position", None))
        return {"fen": chess.STARTING_FEN}


class DirectLoop:
    """``call_soon_threadsafe`` run at once : the stream reader is tested alone."""

    def call_soon_threadsafe(self, fn: Any, *args: Any) -> None:
        fn(*args)


def test_the_viewer_is_the_server_by_default() -> None:
    bridge = Bridge(VIEWER, "g1", chess.WHITE, "t")
    assert isinstance(bridge.server, ViewerServer)
    assert (bridge.server.viewer, bridge.server.game_id) == (VIEWER, "g1")


def test_the_viewer_server_follows_the_bridge_s_game() -> None:
    """Built at each call : a game id changed afterwards is the one played."""
    bridge = Bridge(VIEWER, "g1", chess.WHITE, "t")
    bridge.game_id = "g2"
    assert bridge.server.game_id == "g2"


def test_another_server_gets_the_five_points() -> None:
    payload = {"fen": chess.STARTING_FEN, "ply": 0, "turn": "white", "san_history": []}
    fake = FakeServer([b"data: " + json.dumps(payload).encode() + b"\n"])
    bridge = Bridge(VIEWER, "g1", chess.WHITE, "t")
    bridge.game_server = fake

    assert bridge.post_move("e2e4") == (None, False)
    assert bridge._post_resign() == (200, "")
    assert bridge.seat() == (200, {"colors": ["black"]})
    assert bridge.server_position() == {"fen": chess.STARTING_FEN}

    seen: list[dict] = []
    bridge.apply_stream = seen.append  # type: ignore[method-assign]
    bridge.stream_opened = lambda back=False: bridge.stopped.set()  # type: ignore[method-assign]
    bridge._follow_stream(DirectLoop())  # type: ignore[arg-type]

    assert [name for name, _ in fake.calls] == ["move", "resign", "seat", "position", "stream"]
    assert fake.calls[0] == ("move", "e2e4")


@pytest.mark.parametrize(
    ("call", "url"),
    [
        (lambda s: s.post_move("e2e4"), f"{VIEWER}/api/games/g%201/move"),
        (lambda s: s.post_resign(), f"{VIEWER}/api/games/g%201/resign"),
    ],
)
def test_the_viewer_server_posts_where_the_bridge_did(monkeypatch, call, url) -> None:
    posted: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(viewer, "post", lambda u, t, p=None: posted.append((u, t, p)) or (200, ""))
    call(ViewerServer(VIEWER, "g 1", "tok"))
    assert posted[0][:2] == (url, "tok")


def test_the_viewer_server_reads_its_seat_stream_and_position(monkeypatch) -> None:
    asked: list[Any] = []
    monkeypatch.setattr(viewer, "get_with_token", lambda u, t: asked.append(u) or (200, {}))
    monkeypatch.setattr(viewer, "position", lambda v, g: asked.append((v, g)) or None)
    monkeypatch.setattr(
        server_module.urllib.request,
        "urlopen",
        lambda u, timeout: asked.append((u, timeout)) or contextlib.nullcontext([]),
    )
    srv = ViewerServer(VIEWER, "g1", "tok")
    srv.seat()
    srv.position()
    srv.open_stream()
    assert asked == [
        f"{VIEWER}/api/games/g1/seat",
        (VIEWER, "g1"),
        (f"{VIEWER}/api/games/g1/stream", server_module.SSE_READ_TIMEOUT),
    ]
