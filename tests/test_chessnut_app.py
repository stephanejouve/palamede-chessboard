"""« Échiquier » app (#78, part 1) : state shown to the page, local page server."""

from __future__ import annotations

import http.client
import json
import logging
import sys
import threading
from pathlib import Path

import chess
import pytest
from palamede_chessnut import app as app_module
from palamede_chessnut import bridge as bridge_module
from palamede_chessnut.bridge import Bridge, cancel_on_sigterm
from palamede_chessnut.cli import bridge_from_args
from palamede_chessnut.page import CHESSGROUND, CONTENT_SECURITY_POLICY, PageServer
from palamede_chessnut.status import RecentLog, snapshot, sync_light

import palamede_chessboard

TOKEN = "secret-token-never-shown"
VIEWER_VENDOR = Path(palamede_chessboard.__file__).parent / "viewer" / "vendor" / "chessground"


def stream(board: chess.Board) -> dict:
    replay = board.root()
    return {
        "fen": board.fen(),
        "ply": board.ply(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "san_history": [replay.san_and_push(m) for m in board.move_stack],
        "is_game_over": False,
    }


def bridge_on(board: chess.Board, player: chess.Color = chess.WHITE) -> Bridge:
    bridge = Bridge("https://viewer.test", "g1", player, TOKEN)
    bridge.apply_stream(stream(board))
    return bridge


def e4() -> chess.Board:
    board = chess.Board()
    board.push_san("e4")
    return board


# ---- state shown to the page ----------------------------------------------


def test_snapshot_before_anything_is_known() -> None:
    state = snapshot(Bridge("https://viewer.test", "g1", chess.BLACK, TOKEN))
    assert state["fen"] is None and state["board_fen"] is None
    assert state["color"] == "black"
    assert state["differing"] == [] and state["history"] == []
    assert state["lights"] == {"bluetooth": "searching", "server": "connecting", "sync": "waiting"}


def test_snapshot_shows_the_squares_to_fix_and_the_moves() -> None:
    bridge = bridge_on(e4())
    bridge.observed = chess.BaseBoard()  # the board still shows the start
    state = snapshot(bridge)
    assert state["fen"] == e4().fen()
    assert state["history"] == ["e4"]
    assert state["differing"] == ["e2", "e4"]
    assert state["lights"]["sync"] == "fixing"


def test_sync_light() -> None:
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    assert sync_light(bridge) == "waiting"
    bridge.apply_stream(stream(chess.Board()))
    assert sync_light(bridge) == "waiting"  # no frame from the board yet
    bridge.observed = chess.BaseBoard()
    assert sync_light(bridge) == "on"
    bridge.observed = chess.BaseBoard(e4().board_fen())
    assert sync_light(bridge) == "fixing"


def test_link_lights_follow_the_bridge() -> None:
    bridge = bridge_on(chess.Board())
    bridge.stream_opened()
    bridge.board_link = "connected"
    assert snapshot(bridge)["lights"]["server"] == "on"
    assert snapshot(bridge)["lights"]["bluetooth"] == "on"
    bridge.stream_link = "reconnecting"
    assert snapshot(bridge)["lights"]["server"] == "reconnecting"


def test_the_token_never_reaches_the_page() -> None:
    recent = RecentLog()
    logging.getLogger("palamede_chessnut").addHandler(recent)
    try:
        bridge = bridge_on(e4())
        bridge.observed = chess.BaseBoard()
        assert TOKEN not in json.dumps(snapshot(bridge, recent))
    finally:
        logging.getLogger("palamede_chessnut").removeHandler(recent)


def test_recent_log_keeps_the_last_lines() -> None:
    recent = RecentLog(capacity=2)
    logger = logging.getLogger("test.recent")
    logger.addHandler(recent)
    logger.setLevel(logging.INFO)
    try:
        for n in range(3):
            logger.info("line %d", n)
    finally:
        logger.removeHandler(recent)
    assert [line.split(" ", 2)[2] for line in recent.lines] == ["line 1", "line 2"]


# ---- local page server ----------------------------------------------------


@pytest.fixture
def page():
    server = PageServer(bridge_on(e4()), RecentLog())
    server.start()
    yield server
    server.shutdown()
    server.server_close()


def get(server: PageServer, path: str, host: str | None = None) -> http.client.HTTPResponse:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    headers = {"Host": host} if host is not None else {}
    connection.request("GET", path, headers=headers)
    return connection.getresponse()


def test_page_listens_on_the_loopback_only(page) -> None:
    assert page.server_address[0] == "127.0.0.1"
    assert page.url == f"http://127.0.0.1:{page.port}/"


def test_page_serves_the_state(page) -> None:
    response = get(page, "/state")
    assert response.status == 200
    assert response.getheader("Content-Security-Policy") == CONTENT_SECURITY_POLICY
    assert response.getheader("Cache-Control") == "no-store"
    assert json.loads(response.read())["history"] == ["e4"]


@pytest.mark.parametrize(
    "path",
    ["/", "/app.js", "/app.css", "/vendor/chessground.min.js", "/vendor/chessground.base.css"],
)
def test_page_serves_its_files(page, path: str) -> None:
    response = get(page, path)
    assert response.status == 200
    assert response.read()


def test_page_refuses_another_host(page) -> None:
    # A rebound DNS name reaching the port must read nothing.
    assert get(page, "/state", host=f"evil.test:{page.port}").status == 421
    assert get(page, "/state", host=f"localhost:{page.port}").status == 200


@pytest.mark.parametrize("path", ["/missing", "/../page.py", "/vendor/LICENSE"])
def test_page_serves_nothing_else(page, path: str) -> None:
    assert get(page, path).status == 404


def test_chessground_copy_matches_the_viewer() -> None:
    """The bridge ships its own copy : it must not drift from the viewer's."""
    names = sorted(p.name for p in CHESSGROUND.iterdir())
    assert names == sorted(
        [
            "LICENSE",
            "chessground.base.css",
            "chessground.brown.css",
            "chessground.cburnett.css",
            "chessground.min.js",
        ]
    )
    for name in names:
        assert (CHESSGROUND / name).read_bytes() == (VIEWER_VENDOR / name).read_bytes(), name


# ---- running beside a window ----------------------------------------------


def test_bridge_runs_outside_the_main_thread(monkeypatch) -> None:
    """The app's thread has no signal handler : the bridge must run anyway (#78)."""

    async def no_board(self) -> None:
        self.stopped.set()

    monkeypatch.setattr(Bridge, "run_board", no_board)
    monkeypatch.setattr(Bridge, "_follow_stream", lambda self, loop: None)
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    errors: list[BaseException] = []

    def body() -> None:
        try:
            app_module.run_bridge(bridge)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=body)
    thread.start()
    thread.join(timeout=5)
    assert errors == []
    assert bridge.stopped.is_set()


class Task:
    def cancel(self) -> None:
        pass


@pytest.mark.parametrize("error", [NotImplementedError, RuntimeError, ValueError])
def test_signal_handler_missing_is_not_fatal(error: type[Exception]) -> None:
    """Windows' loop (NotImplementedError), or a thread other than the main one."""

    class Loop:
        def add_signal_handler(self, *_args) -> None:
            raise error

    cancel_on_sigterm(Loop(), Task())  # type: ignore[arg-type]


def test_sigterm_cancels_the_bridge_where_available() -> None:
    added: list = []

    class Loop:
        def add_signal_handler(self, *args) -> None:
            added.append(args)

    task = Task()
    cancel_on_sigterm(Loop(), task)  # type: ignore[arg-type]
    assert added == [(bridge_module.signal.SIGTERM, task.cancel)]


def test_run_bridge_stops_on_an_error(monkeypatch) -> None:
    async def broken(self) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(Bridge, "run", broken)
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    app_module.run_bridge(bridge)
    assert bridge.stopped.is_set()


def test_app_without_pywebview_says_what_to_install(monkeypatch) -> None:
    monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", TOKEN)
    monkeypatch.setitem(sys.modules, "webview", None)
    with pytest.raises(SystemExit, match=r"palamede-chessnut\[app\]"):
        app_module.main(["--viewer", "https://viewer.test", "--game", "g1", "--color", "white"])


def test_cli_and_app_share_their_options(monkeypatch) -> None:
    monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", TOKEN)
    bridge = bridge_from_args(
        ["--viewer", "https://viewer.test/", "--game", "g1", "--color", "black"], "test"
    )
    assert (bridge.viewer, bridge.game_id, bridge.player) == ("https://viewer.test", "g1", False)
    with pytest.raises(SystemExit):
        bridge_from_args(
            ["--viewer", "http://viewer.test", "--game", "g1", "--color", "white"], "t"
        )
