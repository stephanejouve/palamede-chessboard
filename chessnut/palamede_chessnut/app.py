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
:mod:`palamede_chessnut.games`.
"""

from __future__ import annotations

import asyncio
import logging

from palamede_chessnut.bridge import Bridge
from palamede_chessnut.cli import bridge_from_args
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

        # The title of the session's bridge, not of the one told : two crossing
        # openings may tell them out of order (review Junior #123).
        session.on_change = lambda _new: window.set_title(title(session.bridge))
        webview.start(menu=[Menu("Fichier", [MenuAction("Ouvrir une partie…", page.request_open)])])
    finally:
        # Also when the window cannot open (no graphical session, review
        # Junior #87) : the bridge switches the LEDs off before the exit.
        session.close()
        page.shutdown()
        page.server_close()
