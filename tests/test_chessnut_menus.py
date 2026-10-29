"""The window's menus (#140) : the page's actions, and copies on the system clipboard."""

from __future__ import annotations

import http.client
import json

import chess
import pytest
from palamede_chessnut import app as app_module
from palamede_chessnut import clipboard, export
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.page import MENU_ACTIONS, PageServer

TOKEN = "secret-token-never-shown"
VIEWER = "https://viewer.test"
GAME = "tablier.stephane.20261029-211945-e6e0"


@pytest.fixture
def page():
    server = PageServer(Bridge(VIEWER, GAME, chess.WHITE, TOKEN))
    server.start()
    yield server
    server.shutdown()
    server.server_close()


def state_of(server: PageServer) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    connection.request("GET", "/state")
    return json.loads(connection.getresponse().read())


def fake_menu(title, items):
    return (title, items)


def fake_action(title, function):
    return (title, function)


def items(page: PageServer, copy=None) -> dict[str, object]:
    """``{label: function}`` of every menu item."""
    return {
        label: function
        for _title, entries in app_module.menus(page, fake_menu, fake_action, copy)
        for label, function in entries
    }


# ---- the menus and their counters -------------------------------------------


def test_the_menus_and_their_items(page) -> None:
    menus = app_module.menus(page, fake_menu, fake_action)
    assert [(title, [label for label, _f in entries]) for title, entries in menus] == [
        ("Fichier", ["Ouvrir une partie…", "Enregistrer le PGN…"]),
        ("Édition", ["Copier le nom de la partie", "Copier la FEN", "Copier le PGN"]),
        ("Présentation", ["Retourner l'échiquier"]),
    ]


@pytest.mark.parametrize(
    ("label", "action"),
    [
        ("Ouvrir une partie…", "open"),
        ("Enregistrer le PGN…", "save-pgn"),
        ("Retourner l'échiquier", "flip"),
    ],
)
def test_a_menu_item_bumps_its_counter_in_the_state(page, label, action) -> None:
    before = state_of(page)["menu"]
    items(page)[label]()
    after = state_of(page)["menu"]
    assert after[action] == before[action] + 1
    assert {k: v for k, v in after.items() if k != action} == {
        k: v for k, v in before.items() if k != action
    }


def test_open_requests_stays_for_the_open_menu(page) -> None:
    page.request_open()
    state = state_of(page)
    assert state["open_requests"] == state["menu"]["open"] == 1


def test_an_unknown_menu_action_is_refused(page) -> None:
    with pytest.raises(ValueError):
        page.request_menu("format-disk")
    assert set(page.menu_requests) == set(MENU_ACTIONS)


# ---- copies --------------------------------------------------------------------


class Clipboard:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.copied: list[str] = []

    def __call__(self, text: str) -> bool:
        self.copied.append(text)
        return self.ok


def test_copy_the_name_on_the_system_clipboard(page) -> None:
    board = Clipboard()
    before = state_of(page)
    items(page, board)["Copier le nom de la partie"]()
    after = state_of(page)
    assert board.copied == [GAME]
    assert after["notice"] == {"n": before["notice"]["n"] + 1, "text": "Nom copié."}
    assert after["menu"]["copy-name"] == before["menu"]["copy-name"]  # the page has nothing to do


def test_copy_the_pgn_reads_the_viewer_without_the_token(page, monkeypatch) -> None:
    asked = []
    monkeypatch.setattr(
        export,
        "raw_pgn",
        lambda viewer_url, game_id: asked.append((viewer_url, game_id))
        or (200, {"pgn": "1. e4 *"}),
    )
    board = Clipboard()
    items(page, board)["Copier le PGN"]()
    assert board.copied == ["1. e4 *"]
    assert asked == [(VIEWER, GAME)]


def test_copy_without_a_system_clipboard_asks_the_page(page) -> None:
    before = state_of(page)
    items(page, None)["Copier la FEN"]()
    after = state_of(page)
    assert after["menu"]["copy-fen"] == before["menu"]["copy-fen"] + 1
    assert after["notice"] == before["notice"]


def test_a_refused_system_copy_falls_back_to_the_page(page) -> None:
    board = Clipboard(ok=False)
    items(page, board)["Copier le nom de la partie"]()
    assert state_of(page)["menu"]["copy-name"] == 1


def test_nothing_to_copy_falls_back_to_the_page(page) -> None:
    """No FEN before the game is read : the page tries, and says why."""
    board = Clipboard()
    items(page, board)["Copier la FEN"]()
    assert board.copied == []
    assert state_of(page)["menu"]["copy-fen"] == 1


def test_text_to_copy(page, monkeypatch) -> None:
    assert page.text_to_copy("name") == GAME
    assert page.text_to_copy("fen") is None  # no game read yet
    monkeypatch.setattr(export, "raw_pgn", lambda *_a: (502, {"error": "x"}))
    assert page.text_to_copy("pgn") is None
    with pytest.raises(ValueError):
        page.text_to_copy("token")


def test_system_copy_is_none_without_appkit(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def no_appkit(name, *args, **kwargs):
        if name == "AppKit":
            raise ImportError("no AppKit here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_appkit)
    assert clipboard.system_copy() is None
