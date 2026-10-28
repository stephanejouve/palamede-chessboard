"""« Fichier ▸ Ouvrir » in the app : another game of the player, same window (#90)."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import types

import chess
import pytest
from palamede_chessnut import app as app_module
from palamede_chessnut import games, viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.games import NO_ACCOUNT, NO_SERVER, NOT_PLAYED, Session
from palamede_chessnut.page import PageServer
from palamede_chessnut.status import RecentLog

TOKEN = "secret-token-never-shown"
LISTING = {
    "in_progress": [
        {"game_id": "008d", "white": "stephane", "black": "relais", "result": "*", "ply": 82},
        {"game_id": "009", "white": "cd", "black": "stephane", "result": "*"},
    ],
    "completed": [{"game_id": "007", "white": "stephane", "black": "cd", "result": "1-0"}],
}


def running_bridge(game_id: str = "008d", player: chess.Color = chess.WHITE) -> Bridge:
    bridge = Bridge("https://viewer.test", game_id, player, TOKEN, with_board=False)
    bridge.account = "stephane"
    bridge.token_light = "on"
    return bridge


class Runs(list):
    """The session's ``run`` : records each bridge, waits until it is stopped."""

    def __call__(self, bridge: Bridge) -> None:
        self.append(bridge)
        bridge.stopped.wait(5)


@pytest.fixture
def session():
    runs = Runs()
    changes: list[Bridge] = []
    started = Session(running_bridge(), runs, changes.append)
    started.start()
    started.runs = runs  # type: ignore[attr-defined]
    started.changes = changes  # type: ignore[attr-defined]
    yield started
    started.stop()


def answer_seat(monkeypatch, status: int | None, seat: dict | None = None) -> list[str]:
    """``/seat`` answers ``(status, seat)`` ; returns the URLs and tokens asked."""
    asked: list[str] = []

    def seat_of(self: Bridge) -> tuple[int | None, dict | None]:
        asked.append(f"{self.game_id} {self._token}")
        return status, seat

    monkeypatch.setattr(Bridge, "seat", seat_of)
    return asked


# ---- the list -------------------------------------------------------------


def test_player_games_keeps_the_games_in_progress(monkeypatch) -> None:
    urls: list[str] = []
    monkeypatch.setattr(viewer, "get_json", lambda url: urls.append(url) or LISTING)
    assert games.player_games("https://viewer.test", "stéphane jouve") == [
        {"game_id": "008d", "white": "stephane", "black": "relais"},
        {"game_id": "009", "white": "cd", "black": "stephane"},
    ]
    assert urls == ["https://viewer.test/api/games?player=st%C3%A9phane%20jouve"]


@pytest.mark.parametrize(
    "answer", [OSError("down"), ValueError("not JSON"), {}, [], {"in_progress": 3}]
)
def test_player_games_unreadable(monkeypatch, answer) -> None:
    def get_json(_url: str):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(viewer, "get_json", get_json)
    assert games.player_games("https://viewer.test", "stephane") is None


def test_no_game_in_progress(monkeypatch) -> None:
    monkeypatch.setattr(viewer, "get_json", lambda _url: {"in_progress": []})
    assert games.player_games("https://viewer.test", "stephane") == []


def test_the_list_waits_for_the_tokens_account(session) -> None:
    session.bridge.account = None
    assert session.games() == (409, {"error": NO_ACCOUNT})


def test_the_list_when_the_server_does_not_answer(session, monkeypatch) -> None:
    monkeypatch.setattr(games, "player_games", lambda *_a: None)
    assert session.games() == (502, {"error": NO_SERVER})


def test_the_list_names_the_open_game(session, monkeypatch) -> None:
    monkeypatch.setattr(viewer, "get_json", lambda _url: LISTING)
    status, body = session.games()
    assert status == 200
    assert body["account"] == "stephane"
    assert body["current"] == "008d"
    assert [g["game_id"] for g in body["games"]] == ["008d", "009"]
    assert TOKEN not in json.dumps(body)


# ---- the colour -----------------------------------------------------------


@pytest.mark.parametrize(
    ("colors", "current", "chosen"),
    [
        (["black"], chess.WHITE, chess.BLACK),
        (["white"], chess.BLACK, chess.WHITE),
        (["white", "black"], chess.BLACK, chess.BLACK),
        (["white", "black"], chess.WHITE, chess.WHITE),
        ([], chess.WHITE, None),
        (["purple"], chess.WHITE, None),
    ],
)
def test_the_colour_is_the_one_played_there(colors, current, chosen) -> None:
    assert games.chosen_color(colors, current) == chosen


# ---- opening a game -------------------------------------------------------


def test_opening_a_game_replaces_the_bridge(session, monkeypatch) -> None:
    old = session.bridge
    asked = answer_seat(monkeypatch, 200, {"player": "stephane", "colors": ["black"]})
    assert session.open_game("009") == (200, "")
    new = session.bridge
    assert old.stopped.is_set()
    assert new is not old and not new.stopped.is_set()
    assert (new.game_id, new.player, new.viewer) == ("009", chess.BLACK, old.viewer)
    assert new.with_board == old.with_board and new.board_transport == old.board_transport
    # The same token, asked for the new game before anything else changed.
    assert asked == [f"009 {TOKEN}"]
    assert session.changes == [new]
    for _ in range(50):
        if session.runs[-1] is new:
            break
        threading.Event().wait(0.02)
    assert session.runs[-1] is new


@pytest.mark.parametrize(
    ("status", "seat", "answer"),
    [
        (401, None, 403),
        (403, None, 403),
        (404, None, 404),
        (None, None, 502),
        (500, None, 502),
        (200, ["not", "a", "seat"], 502),
        (200, {"player": "stephane", "colors": []}, 403),
    ],
)
def test_a_refused_game_leaves_the_running_one(session, monkeypatch, status, seat, answer) -> None:
    old = session.bridge
    answer_seat(monkeypatch, status, seat)
    code, message = session.open_game("009")
    assert code == answer and message
    assert session.bridge is old and not old.stopped.is_set()
    assert session.changes == []
    if status == 200 and isinstance(seat, dict):
        assert message == NOT_PLAYED
    if answer == 502:
        assert message == NO_SERVER


@pytest.mark.parametrize("game_id", ["", "../etc", "a b", ".hidden", "x" * 65, "008d?x=1"])
def test_a_malformed_game_id_is_not_asked(session, monkeypatch, game_id: str) -> None:
    asked = answer_seat(monkeypatch, 200, {"colors": ["white"]})
    assert session.open_game(game_id)[0] == 400
    assert asked == []


def test_opening_the_open_game_changes_nothing(session, monkeypatch) -> None:
    old = session.bridge
    asked = answer_seat(monkeypatch, 200, {"colors": ["white"]})
    assert session.open_game("008d") == (200, "")
    assert session.bridge is old and not old.stopped.is_set()
    assert asked == []


def test_opening_after_the_game_ended(session, monkeypatch) -> None:
    """The bridge stops by itself after a finished game : the window may still open another."""
    session.bridge.stopped.set()
    answer_seat(monkeypatch, 200, {"colors": ["white"]})
    assert session.open_game("009") == (200, "")
    assert not session.bridge.stopped.is_set()


# ---- through the page -----------------------------------------------------


@pytest.fixture
def page(session):
    server = PageServer(session.bridge, RecentLog())
    server.session = session
    session.on_change = lambda new: setattr(server, "bridge", new)
    server.start()
    yield server
    server.shutdown()
    server.server_close()


def request(server: PageServer, method: str, path: str, body: bytes | None = None, **headers):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    return response.status, response.read()


def good_headers(server: PageServer) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.port}",
        "X-Palamede-Nonce": server.nonce,
        "Content-Type": "application/json",
    }


OPEN = json.dumps({"game_id": "009"}).encode()


def test_the_page_lists_and_opens(page, monkeypatch) -> None:
    monkeypatch.setattr(viewer, "get_json", lambda _url: LISTING)
    answer_seat(monkeypatch, 200, {"colors": ["black"]})
    status, body = request(page, "GET", "/games")
    assert status == 200 and json.loads(body)["current"] == "008d"
    status, body = request(page, "POST", "/open", OPEN, **good_headers(page))
    assert (status, json.loads(body)) == (200, {"ok": True})
    state = json.loads(request(page, "GET", "/state")[1])
    assert (state["game_id"], state["color"]) == ("009", "black")


def test_the_page_says_why_a_game_is_not_opened(page, monkeypatch) -> None:
    answer_seat(monkeypatch, 403, None)
    status, body = request(page, "POST", "/open", OPEN, **good_headers(page))
    assert (status, json.loads(body)) == (403, {"error": NOT_PLAYED})


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"X-Palamede-Nonce": None}, 403),
        ({"Origin": "https://evil.test"}, 403),
        ({"Host": "evil.test:1"}, 421),
        ({"Content-Type": "text/plain"}, 415),
    ],
)
def test_opening_needs_the_page(page, monkeypatch, change: dict, status: int) -> None:
    asked = answer_seat(monkeypatch, 200, {"colors": ["white"]})
    headers = {**good_headers(page), **change}
    assert (
        request(page, "POST", "/open", OPEN, **{k: v for k, v in headers.items() if v})[0] == status
    )
    assert asked == []
    assert page.bridge.game_id == "008d"


@pytest.mark.parametrize("body", [b'{"uci": "e2e4"}', b'{"game_id": 9}', b"[]"])
def test_open_without_a_game_id(page, body: bytes) -> None:
    status, answer = request(page, "POST", "/open", body, **good_headers(page))
    assert (status, json.loads(answer)) == (400, {"error": "partie illisible"})


def test_without_a_session_there_is_no_list_nor_open() -> None:
    """The bare page (CLI tests, older callers) : the routes do not exist."""
    server = PageServer(running_bridge(), RecentLog())
    server.start()
    try:
        assert request(server, "GET", "/games")[0] == 404
        assert request(server, "POST", "/open", OPEN, **good_headers(server))[0] == 404
    finally:
        server.shutdown()
        server.server_close()


def test_the_menu_asks_the_page_for_its_list(page) -> None:
    before = json.loads(request(page, "GET", "/state")[1])["open_requests"]
    page.request_open()
    assert json.loads(request(page, "GET", "/state")[1])["open_requests"] == before + 1


# ---- the window -----------------------------------------------------------


def test_the_window_has_a_fichier_menu_and_follows_the_game(monkeypatch) -> None:
    monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", TOKEN)
    monkeypatch.setattr(app_module, "run_bridge", Runs())
    titles: list[str] = []
    seen: dict = {}

    class Window:
        def set_title(self, title: str) -> None:
            titles.append(title)

    webview = types.ModuleType("webview")
    webview.create_window = lambda *_a, **_k: Window()  # type: ignore[attr-defined]
    menu = types.ModuleType("webview.menu")
    menu.Menu = lambda title, items: (title, items)  # type: ignore[attr-defined]
    menu.MenuAction = lambda title, function: (title, function)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setitem(sys.modules, "webview.menu", menu)
    answer_seat(monkeypatch, 200, {"colors": ["white"]})
    pages: list[PageServer] = []
    monkeypatch.setattr(
        app_module, "PageServer", lambda *a: pages.append(PageServer(*a)) or pages[-1]
    )

    def start(**kwargs) -> None:
        seen.update(kwargs)
        (title, [(label, action)]) = kwargs["menu"][0]
        assert (title, label) == ("Fichier", "Ouvrir une partie…")
        action()
        assert pages[0].open_requests == 1
        assert pages[0].session.open_game("009") == (200, "")

    webview.start = start  # type: ignore[attr-defined]
    app_module.main(["--viewer", "https://viewer.test", "--game", "008d", "--color", "white"])
    assert "menu" in seen
    assert titles == ["Échiquier — 009"]
    assert pages[0].bridge.game_id == "009"
    assert pages[0].bridge.stopped.is_set()


def test_no_second_bridge_while_the_first_still_runs(session, monkeypatch) -> None:
    """A bridge slow to stop keeps the board : no new one beside it (review Admin #121)."""
    old = session.bridge
    release = threading.Event()
    stuck = threading.Thread(target=release.wait, args=(5,), daemon=True)
    stuck.start()
    session.worker = stuck
    monkeypatch.setattr(games, "STOP_SECONDS", 0.05)
    answer_seat(monkeypatch, 200, {"colors": ["white"]})
    assert session.open_game("009") == (503, games.BOARD_BUSY)
    assert session.bridge is old and old.stopped.is_set()
    assert session.changes == []
    release.set()
    stuck.join(1)
    assert session.open_game("009") == (200, "")
    assert session.bridge.game_id == "009"


def test_the_stopped_game_can_be_opened_again(session, monkeypatch) -> None:
    """After a 503, the game left on screen is stopped : choosing it restarts it (Junior #121)."""
    old = session.bridge
    old.stopped.set()
    asked = answer_seat(monkeypatch, 200, {"colors": ["white"]})
    assert session.open_game("008d") == (200, "")
    assert session.bridge is not old and session.bridge.game_id == "008d"
    assert not session.bridge.stopped.is_set()
    assert asked == [f"008d {TOKEN}"]


def test_no_game_opens_once_the_window_closes(session, monkeypatch) -> None:
    asked = answer_seat(monkeypatch, 200, {"colors": ["white"]})
    session.close()
    assert session.bridge.stopped.is_set()
    assert session.open_game("009")[0] == 503
    assert asked == [] and session.changes == []


def test_closing_waits_for_an_opening_under_way(session, monkeypatch) -> None:
    """The window closes while /seat is asked : the bridge that opening starts is stopped too."""
    asking = threading.Event()
    answer = threading.Event()

    def slow_seat(_self: Bridge):
        asking.set()
        answer.wait(5)
        return 200, {"colors": ["white"]}

    monkeypatch.setattr(Bridge, "seat", slow_seat)
    opening = threading.Thread(target=session.open_game, args=("009",))
    opening.start()
    assert asking.wait(5)
    closing = threading.Thread(target=session.close)
    closing.start()
    answer.set()
    opening.join(5)
    closing.join(5)
    assert session.bridge.game_id == "009"
    assert session.bridge.stopped.is_set()


@pytest.mark.parametrize("transport", ["usb", "ble"])
def test_the_new_bridge_keeps_the_board(monkeypatch, transport: str) -> None:
    """With a board, the opened game is played on it too (mutant of Junior, #121)."""
    runs = Runs()
    old = Bridge(
        "https://viewer.test",
        "008d",
        chess.WHITE,
        TOKEN,
        with_board=True,
        board_transport=transport,
    )
    board_session = Session(old, runs)
    board_session.start()
    answer_seat(monkeypatch, 200, {"colors": ["white"]})
    try:
        assert board_session.open_game("009") == (200, "")
        new = board_session.bridge
        assert (new.with_board, new.board_transport) == (True, transport)
    finally:
        board_session.stop()
