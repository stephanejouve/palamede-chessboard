"""The « Échiquier » app (#78, part 1) : the bridge with a window, read-only.

    palamede-chessnut-app --viewer <viewer URL> --game <game_id> --color white

Same options as ``palamede-chessnut``. The window (pywebview : WebKit on
macOS, Qt on Linux, Edge WebView2 on Windows) shows the local page
of :mod:`palamede_chessnut.page`. pywebview needs the main thread for its
window, so the bridge runs beside it, in a thread with its own event loop.
Closing the window stops the bridge, LEDs off.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from palamede_chessnut.bridge import Bridge
from palamede_chessnut.cli import bridge_from_args
from palamede_chessnut.page import PageServer
from palamede_chessnut.status import RecentLog

log = logging.getLogger(__name__)

#: Time left to the bridge to switch the LEDs off once the window is closed.
STOP_SECONDS = 5.0
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


def main(argv: list[str] | None = None) -> None:
    bridge = bridge_from_args(argv, "Play a viewer game on a Chessnut Air+ board, in a window.")
    try:
        import webview
    except ImportError:
        raise SystemExit(MISSING_WEBVIEW) from None
    recent = RecentLog()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger().addHandler(recent)
    page = PageServer(bridge, recent)
    page.start()
    worker = threading.Thread(target=run_bridge, args=(bridge,), daemon=True)
    worker.start()
    webview.create_window(f"Échiquier — {bridge.game_id}", page.url, width=900, height=640)
    webview.start()
    bridge.stopped.set()
    worker.join(timeout=STOP_SECONDS)
    page.shutdown()
