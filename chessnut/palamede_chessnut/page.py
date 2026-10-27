"""Local page of the app (#78) : served on the loopback only, read-only.

The page polls ``/state`` (:func:`palamede_chessnut.status.snapshot`) ; it
never receives the player token, which stays in the bridge. Requests whose
``Host`` is not this loopback address are refused, so that a web page
reaching the port through a rebound DNS name reads nothing.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from palamede_chessnut.status import RecentLog, snapshot

if TYPE_CHECKING:
    from palamede_chessnut.bridge import Bridge

HERE = Path(__file__).parent
#: chessground, copied from the viewer's vendor directory (same version, MIT).
CHESSGROUND = HERE / "vendor" / "chessground"
PAGE = HERE / "web"
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
#: chessground's pieces are SVG data URIs in its CSS ; everything else is local.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
    ]
)


def static_files() -> dict[str, Path]:
    """URL path → file : the page, its script and style, chessground."""
    files = {"/": PAGE / "index.html"}
    files.update({f"/{f.name}": f for f in PAGE.iterdir() if f.suffix in (".js", ".css")})
    files.update(
        {f"/vendor/{f.name}": f for f in CHESSGROUND.iterdir() if f.suffix in (".js", ".css")}
    )
    return files


class PageServer(http.server.ThreadingHTTPServer):
    """``127.0.0.1`` on a free port ; :attr:`url` is what the window opens."""

    daemon_threads = True

    def __init__(self, bridge: Bridge, recent: RecentLog | None = None) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.bridge = bridge
        self.recent = recent
        self.files = static_files()
        self.port = int(self.server_address[1])
        self.url = f"http://127.0.0.1:{self.port}/"

    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread


class _Handler(http.server.BaseHTTPRequestHandler):
    server: PageServer

    def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
        if self.headers.get("Host") not in self.server.allowed_hosts():
            self._send(421, "text/plain", b"wrong host")
            return
        path = self.path.split("?", 1)[0]
        if path == "/state":
            body = json.dumps(snapshot(self.server.bridge, self.server.recent))
            self._send(200, "application/json", body.encode("utf-8"))
            return
        file = self.server.files.get(path)
        if file is None:
            self._send(404, "text/plain", b"not found")
            return
        self._send(200, TYPES[file.suffix], file.read_bytes())

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silent : the page polls twice a second, the journal is the bridge's."""
