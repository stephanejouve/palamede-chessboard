"""The game's name and PGN for the page (#134) : copied, or saved to a file."""

from __future__ import annotations

import http.client
import json
import types
import urllib.error

import pytest
from palamede_chessnut import app as app_module
from palamede_chessnut import export, viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.page import PageServer

TOKEN = "secret-token-never-shown"
VIEWER = "https://viewer.test"
GAME = "tablier.stephane.20261029-211945-e6e0"
PGN = '[Event "Tablier"]\n[White "stephane"]\n[Black "bot-maia-1100"]\n\n1. a3 d5 *\n'


class FakeViewer:
    """``viewer.get_json`` by URL ; records what was asked."""

    def __init__(self, monkeypatch) -> None:
        self.answer: object = {"game_id": GAME, "pgn": PGN}
        self.asked: list[str] = []
        monkeypatch.setattr(viewer, "get_json", self.get_json)

        def no_token(*_args, **_kwargs):
            raise AssertionError("the PGN is read without the token")

        monkeypatch.setattr(viewer, "get_with_token", no_token)

    def get_json(self, url: str):
        self.asked.append(url)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def fake(monkeypatch) -> FakeViewer:
    return FakeViewer(monkeypatch)


@pytest.fixture
def page():
    server = PageServer(Bridge(VIEWER, GAME, True, TOKEN))
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


SAVE = json.dumps({"game_id": GAME}).encode()


# ---- the PGN read from the viewer ------------------------------------------


def test_pgn_of_the_game_on_screen(page, fake) -> None:
    status, body = request(page, "GET", "/pgn")
    assert (status, json.loads(body)) == (200, {"game_id": GAME, "pgn": PGN})
    assert fake.asked == [f"{VIEWER}/api/raw?game_id={GAME}"]
    assert TOKEN.encode() not in body


def test_pgn_of_an_unknown_game_is_404(page, fake) -> None:
    fake.answer = urllib.error.HTTPError("u", 404, "not found", None, None)  # type: ignore[arg-type]
    status, body = request(page, "GET", "/pgn")
    assert (status, json.loads(body)) == (404, {"error": export.UNKNOWN})


@pytest.mark.parametrize(
    "failure",
    [OSError("down"), ValueError("not json"), urllib.error.HTTPError("u", 500, "x", None, None)],  # type: ignore[arg-type]
    ids=["injoignable", "illisible", "erreur 500"],
)
def test_pgn_when_the_viewer_fails_is_502(page, fake, failure) -> None:
    fake.answer = failure
    status, body = request(page, "GET", "/pgn")
    assert (status, json.loads(body)) == (502, {"error": export.UNREACHABLE})


@pytest.mark.parametrize("answer", [{"game_id": GAME}, {"pgn": 5}, ["not", "a", "dict"]])
def test_pgn_of_an_unexpected_answer_is_502(page, fake, answer) -> None:
    fake.answer = answer
    status, body = request(page, "GET", "/pgn")
    assert (status, json.loads(body)) == (502, {"error": export.UNREADABLE})


def test_pgn_url_quotes_the_game_id(fake) -> None:
    export.raw_pgn(VIEWER, "a b&c")
    assert fake.asked == [f"{VIEWER}/api/raw?game_id=a%20b%26c"]


def test_the_state_shows_the_game_name(page, fake) -> None:
    state = json.loads(request(page, "GET", "/state")[1])
    assert state["game_id"] == GAME


# ---- saving, the app only --------------------------------------------------


def test_no_save_without_the_app(page, fake) -> None:
    state = json.loads(request(page, "GET", "/state")[1])
    assert state["can_save"] is False
    assert request(page, "POST", "/save-pgn", SAVE, **good_headers(page))[0] == 404


def test_save_writes_the_pgn_where_the_dialog_says(page, fake, tmp_path) -> None:
    asked = []
    target = tmp_path / "choisi.pgn"
    page.ask_path = lambda name: asked.append(name) or str(target)
    assert json.loads(request(page, "GET", "/state")[1])["can_save"] is True
    status, body = request(page, "POST", "/save-pgn", SAVE, **good_headers(page))
    assert (status, json.loads(body)) == (200, {"saved": "choisi.pgn"})
    assert asked == [f"{GAME}.pgn"]
    assert target.read_text(encoding="utf-8") == PGN


def test_save_cancelled_writes_nothing(page, fake, tmp_path) -> None:
    page.ask_path = lambda _name: None
    status, body = request(page, "POST", "/save-pgn", SAVE, **good_headers(page))
    assert (status, json.loads(body)) == (200, {"saved": None})
    assert list(tmp_path.iterdir()) == []


def test_save_of_another_game_is_refused(page, fake, tmp_path) -> None:
    """Another game opened meanwhile : never save the wrong one."""
    asked = []
    page.ask_path = lambda name: asked.append(name) or str(tmp_path / "x.pgn")
    other = json.dumps({"game_id": "008d"}).encode()
    assert request(page, "POST", "/save-pgn", other, **good_headers(page))[0] == 409
    assert asked == [] and fake.asked == []


def test_save_asks_nothing_when_the_viewer_fails(page, fake) -> None:
    asked = []
    page.ask_path = lambda name: asked.append(name) or "/never"
    fake.answer = OSError("down")
    assert request(page, "POST", "/save-pgn", SAVE, **good_headers(page))[0] == 502
    assert asked == []


def test_save_to_an_unwritable_place_is_500(page, fake, tmp_path) -> None:
    page.ask_path = lambda _name: str(tmp_path / "no-such-dir" / "x.pgn")
    status, body = request(page, "POST", "/save-pgn", SAVE, **good_headers(page))
    assert status == 500
    assert "x.pgn" in json.loads(body)["error"]


@pytest.mark.parametrize(
    "change",
    [{"X-Palamede-Nonce": "guess"}, {"Origin": "https://evil.test"}],
    ids=["nonce", "origine"],
)
def test_save_needs_the_nonce_and_the_origin(page, fake, tmp_path, change) -> None:
    asked = []
    page.ask_path = lambda name: asked.append(name) or str(tmp_path / "x.pgn")
    headers = {**good_headers(page), **change}
    assert request(page, "POST", "/save-pgn", SAVE, **headers)[0] == 403
    assert asked == []


# ---- the app's native dialog -----------------------------------------------


class FakeWindow:
    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls: list[tuple] = []

    def create_file_dialog(self, kind, **options):
        self.calls.append((kind, options))
        return self.answer


@pytest.mark.parametrize(
    ("answer", "path"),
    [("/tmp/a.pgn", "/tmp/a.pgn"), (("/tmp/b.pgn",), "/tmp/b.pgn"), (None, None), ((), None)],
    ids=["chaîne", "tuple", "annulé", "tuple vide"],
)
def test_save_dialog_answers(answer, path) -> None:
    webview = types.SimpleNamespace(SAVE_DIALOG=30)
    window = FakeWindow(answer)
    assert app_module.save_dialog(webview, window)(f"{GAME}.pgn") == path
    assert window.calls == [(30, {"save_filename": f"{GAME}.pgn", "file_types": ("PGN (*.pgn)",)})]


def test_save_dialog_prefers_the_new_constant() -> None:
    """pywebview 5.1 : ``FileDialog.SAVE`` ; ``SAVE_DIALOG`` only on 5.0."""
    webview = types.SimpleNamespace(FileDialog=types.SimpleNamespace(SAVE="save"), SAVE_DIALOG=30)
    window = FakeWindow("/tmp/a.pgn")
    app_module.save_dialog(webview, window)("x.pgn")
    assert window.calls[0][0] == "save"


def test_save_dialog_without_any_constant_saves_nothing() -> None:
    """A pywebview with neither constant : no crash, nothing saved, the window opens anyway."""
    window = FakeWindow("/tmp/a.pgn")
    assert app_module.save_dialog(types.SimpleNamespace(), window)("x.pgn") is None
    assert window.calls == []
