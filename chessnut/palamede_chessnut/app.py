"""The « Échiquier » app (#78) : the bridge with a window.

    palamede-chessnut-app [--viewer <viewer URL>] [--game <game_id>] [--color white] [--no-board]

Same options as ``palamede-chessnut``, plus ``--no-board`` : no Chessnut,
the player's moves are made with the mouse in the window (#92). Without
``--game``, the token's newest game in progress (:mod:`palamede_chessnut.launch`).

The window (pywebview : WebKit on macOS, Qt on Linux, Edge WebView2 on
Windows) shows the local page of :mod:`palamede_chessnut.page`. pywebview needs the main thread for its
window, so the bridge runs beside it, in a thread with its own event loop.
Closing the window stops the bridge, LEDs off.

The menu « Fichier ▸ Ouvrir une partie… » lists the player's games in
progress and moves the window to the chosen one (#90), see
:mod:`palamede_chessnut.games`. « Enregistrer le PGN… », « Édition ▸
Copier … » and « Présentation ▸ Retourner l'échiquier » run what the page's
buttons do (#140). The page's « Enregistrer… » saves the
game's PGN through the window's native dialog (#134).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from palamede_chessnut.bridge import Bridge
from palamede_chessnut.cli import bridge_from_args
from palamede_chessnut.clipboard import Copy, system_copy
from palamede_chessnut.export import AskPath
from palamede_chessnut.games import Session
from palamede_chessnut.page import PageServer
from palamede_chessnut.status import RecentLog

log = logging.getLogger(__name__)

MISSING_WEBVIEW = "the app needs pywebview : install palamede-chessnut[app]"


def run_bridge(bridge: Bridge) -> None:
    """Thread body : the bridge until the game ends or :attr:`Bridge.stopped` is set."""
    try:
        asyncio.run(bridge.run())
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("the bridge stopped on an error")
        bridge.stopped.set()


def title(bridge: Bridge) -> str:
    return f"Échiquier — {bridge.game_id}"


def save_dialog(webview: Any, window: Any) -> AskPath:
    """The window's native save dialog (#134) : the chosen path, ``None`` if cancelled."""

    def ask(name: str) -> str | None:
        # pywebview 5.1 moved the constant to ``FileDialog.SAVE`` ; 5.0 has only
        # ``SAVE_DIALOG``. Read at each save : a window never fails to open for it.
        kind = getattr(getattr(webview, "FileDialog", None), "SAVE", None) or getattr(
            webview, "SAVE_DIALOG", None
        )
        if kind is None:
            log.warning("this pywebview has no save dialog : PGN not saved")
            return None
        chosen = window.create_file_dialog(kind, save_filename=name, file_types=("PGN (*.pgn)",))
        # A string, or a tuple of one path, depending on the platform.
        if isinstance(chosen, list | tuple):
            chosen = chosen[0] if chosen else None
        return str(chosen) if chosen else None

    return ask


#: What a menu copy says once done (#140).
COPIED = {"name": "Nom copié.", "fen": "FEN copiée.", "pgn": "PGN copié."}


def copier(page: PageServer, kind: str, copy: Copy | None) -> Any:
    """« Édition ▸ Copier … » : the system clipboard when there is one, else the page tries.

    Off the menu's thread : reading the PGN waits for the viewer, and the
    menu may run on the window's main thread (review Junior #141).
    """

    def run() -> None:
        text = page.text_to_copy(kind) if copy is not None else None
        if text is not None and copy is not None and copy(text):
            page.notify(COPIED[kind])
        else:
            page.request_menu(f"copy-{kind}")

    def start() -> None:
        threading.Thread(target=run, name=f"copy-{kind}", daemon=True).start()

    start.run = run  # type: ignore[attr-defined]  # the same, at once : for the tests
    return start


def menus(page: PageServer, menu: Any, action: Any, copy: Copy | None = None) -> list[Any]:
    """The window's menus (#90, #140) : each item does what the page's button does."""

    def ask(name: str) -> Any:
        return lambda: page.request_menu(name)

    return [
        menu(
            "Fichier",
            [
                action("Ouvrir une partie…", page.request_open),
                action("Enregistrer le PGN…", ask("save-pgn")),
            ],
        ),
        menu(
            "Édition",
            [
                action("Copier le nom de la partie", copier(page, "name", copy)),
                action("Copier la FEN", copier(page, "fen", copy)),
                action("Copier le PGN", copier(page, "pgn", copy)),
            ],
        ),
        menu("Présentation", [action("Retourner l'échiquier", ask("flip"))]),
    ]


def main(argv: list[str] | None = None) -> None:
    bridge = bridge_from_args(
        argv, "Play a viewer game on a Chessnut Air+ board, in a window.", app=True
    )
    try:
        import webview
        from webview.menu import Menu, MenuAction
    except ImportError:
        raise SystemExit(MISSING_WEBVIEW) from None
    recent = RecentLog()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger().addHandler(recent)
    page = PageServer(bridge, recent)
    page.start()
    session = Session(bridge, run_bridge)
    page.session = session
    session.start()
    try:
        # text_select : pywebview disables selection by default, and the FEN or the
        # journal could not be selected (review of Stéphane, 28/10).
        window = webview.create_window(
            title(bridge), page.url, width=900, height=640, text_select=True
        )
        page.ask_path = save_dialog(webview, window)

        # The title of the session's bridge, not of the one told : two crossing
        # openings may tell them out of order (review Junior #123).
        session.on_change = lambda _new: window.set_title(title(session.bridge))
        webview.start(menu=menus(page, Menu, MenuAction, system_copy()))
    finally:
        # Also when the window cannot open (no graphical session, review
        # Junior #87) : the bridge switches the LEDs off before the exit.
        session.close()
        page.shutdown()
        page.server_close()
