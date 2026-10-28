"""Local page of the app (#78) : served on the loopback only.

The page polls ``/state`` (:func:`palamede_chessnut.status.snapshot`) ; it
never receives the player token, which stays in the bridge. Requests whose
``Host`` is not this loopback address are refused, so that a web page
reaching the port through a rebound DNS name reads nothing.

Its only write is ``POST /move``, for mouse play without a board (#92,
frame of Admin on #78) : the ``Origin`` must be this page, and the
``X-Palamede-Nonce`` header must carry the nonce drawn at launch and
written into the page. Another site open in a browser of the machine
knows neither, and a plain form cannot set the header.
"""

from __future__ import annotations

import hmac
import http.server
import json
import secrets
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from palamede_chessnut.mouse import play_from_page
from palamede_chessnut.status import RecentLog, snapshot

if TYPE_CHECKING:
    from palamede_chessnut.bridge import Bridge

HERE = Path(__file__).parent
#: chessground 9.2.1, GPL-3.0-or-later : an identical copy of the viewer's vendor
#: directory, with its licence, provenance and TypeScript source (``src/``).
CHESSGROUND = HERE / "vendor" / "chessground"
PAGE = HERE / "web"
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
#: Replaced in ``index.html`` by the nonce of this launch.
NONCE_PLACEHOLDER = b"__PALAMEDE_NONCE__"
#: ``{"uci": "e7e8q"}`` and a little room : anything bigger is not a move.
MAX_MOVE_BYTES = 256
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
        #: Drawn per launch, only for this page : not a player token.
        self.nonce = secrets.token_urlsafe(32)

    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def allowed_origins(self) -> set[str]:
        return {f"http://{host}" for host in self.allowed_hosts()}

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
        content = file.read_bytes()
        if path == "/":
            content = content.replace(NONCE_PLACEHOLDER, self.server.nonce.encode("ascii"))
        self._send(200, TYPES[file.suffix], content)

    def do_POST(self) -> None:  # noqa: N802 (http.server's naming)
        """``POST /move`` ``{"uci": ...}`` : the move goes through the bridge's checks."""
        if self.headers.get("Host") not in self.server.allowed_hosts():
            self._send(421, "text/plain", b"wrong host")
            return
        if self.path != "/move":
            self._send(404, "text/plain", b"not found")
            return
        if self.headers.get("Origin") not in self.server.allowed_origins():
            self._answer(403, "origine refusée")
            return
        nonce = self.headers.get("X-Palamede-Nonce", "").encode("latin-1")
        if not hmac.compare_digest(nonce, self.server.nonce.encode("ascii")):
            self._answer(403, "nonce refusé")
            return
        uci = self._read_move()
        if uci is None:
            return
        status, message = play_from_page(self.server.bridge, uci)
        self._answer(status, message)

    def _read_move(self) -> str | None:
        """The ``uci`` of the body, or ``None`` once the error is answered."""
        if self.headers.get_content_type() != "application/json":
            self._answer(415, "JSON attendu")
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._answer(411, "longueur absente")
            return None
        if not 0 < length <= MAX_MOVE_BYTES:
            self._answer(413, "corps trop long")
            return None
        try:
            uci = json.loads(self.rfile.read(length))["uci"]
        except (ValueError, KeyError, TypeError):
            uci = None
        if not isinstance(uci, str):
            self._answer(400, "coup illisible")
            return None
        return uci

    def _answer(self, status: int, message: str) -> None:
        body = {"ok": True} if status == 200 else {"error": message}
        self._send(status, "application/json", json.dumps(body).encode("utf-8"))

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
