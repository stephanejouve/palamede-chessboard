"""Local page of the app (#78) : served on the loopback only.

The page polls ``/state`` (:func:`palamede_chessnut.status.snapshot`) ; it
never receives the player token, which stays in the bridge. Requests whose
``Host`` is not this loopback address are refused, so that a web page
reaching the port through a rebound DNS name reads nothing.

Its writes are ``POST /move``, for mouse play without a board (#92,
frame of Admin on #78), ``POST /open``, another game of the player
(#90), and ``POST /save-pgn``, the game's PGN to a file chosen in a native
dialog (#134, the app only) : the ``Origin`` must be this page, and the
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

from palamede_chessnut import export
from palamede_chessnut.clock import ClockCache
from palamede_chessnut.mouse import play_from_page
from palamede_chessnut.status import RecentLog, snapshot

if TYPE_CHECKING:
    from palamede_chessnut.bridge import Bridge
    from palamede_chessnut.games import Session

HERE = Path(__file__).parent
#: chessground 9.2.1, GPL-3.0-or-later : an identical copy of the viewer's vendor
#: directory, with its licence, provenance and TypeScript source (``src/``).
CHESSGROUND = HERE / "vendor" / "chessground"
PAGE = HERE / "web"
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
#: Replaced in ``index.html`` by the nonce of this launch.
NONCE_PLACEHOLDER = b"__PALAMEDE_NONCE__"
#: What the window's menus can ask of the page (#90, #140).
MENU_ACTIONS = ("open", "save-pgn", "copy-name", "copy-fen", "copy-pgn", "flip")
#: ``{"uci": "e7e8q"}`` or ``{"game_id": ...}`` and a little room : anything bigger is refused.
MAX_BODY_BYTES = 256
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
        self._bridge = bridge
        self.recent = recent
        self.files = static_files()
        self.port = int(self.server_address[1])
        self.url = f"http://127.0.0.1:{self.port}/"
        #: Drawn per launch, only for this page : not a player token.
        self.nonce = secrets.token_urlsafe(32)
        #: The app's session (#90) : without it, no game list and no ``/open``.
        self.session: Session | None = None
        #: Bumped by the window's menus (#90, #140) : the page runs the action when
        #: a counter changes, as a click on its button would.
        self.menu_requests = dict.fromkeys(MENU_ACTIONS, 0)
        #: A short message for the page, numbered : « Nom copié. » after a menu copy.
        self.notice: dict[str, Any] = {"n": 0, "text": ""}
        #: The app's save dialog (#134) : without it, no ``/save-pgn``.
        self.ask_path: export.AskPath | None = None
        #: One save at a time : the server is threaded, a double click would
        #: open two dialogs (review Junior #137).
        self.saving = threading.Lock()
        #: The game's clock, read from the viewer once a second at most (#135).
        self.clocks = ClockCache()

    @property
    def bridge(self) -> Bridge:
        """The session's bridge when there is one : read at each request, never a copy
        that two crossing openings could leave behind (review Junior #123)."""
        return self.session.bridge if self.session is not None else self._bridge

    def request_menu(self, action: str) -> None:
        if action not in self.menu_requests:
            raise ValueError(f"unknown menu action {action!r}")
        self.menu_requests[action] += 1

    def notify(self, text: str) -> None:
        self.notice = {"n": self.notice["n"] + 1, "text": text}

    def text_to_copy(self, kind: str) -> str | None:
        """The game's ``name``, ``fen`` or ``pgn`` for a menu copy ; ``None`` if unknown."""
        bridge = self.bridge
        if kind == "name":
            return bridge.game_id
        if kind == "fen":
            return bridge.game.fen() if bridge.game is not None else None
        if kind == "pgn":
            status, body = export.raw_pgn(bridge.viewer, bridge.game_id)
            return body["pgn"] if status == 200 else None
        raise ValueError(f"unknown copy {kind!r}")

    def request_open(self) -> None:
        self.request_menu("open")

    @property
    def open_requests(self) -> int:
        return self.menu_requests["open"]

    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def allowed_origins(self) -> set[str]:
        return {f"http://{host}" for host in self.allowed_hosts()}

    def server_close(self) -> None:
        self.clocks.close()
        super().server_close()

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
            state = snapshot(self.server.bridge, self.server.recent)
            state["open_requests"] = self.server.open_requests
            state["menu"] = dict(self.server.menu_requests)
            state["notice"] = self.server.notice
            state["can_save"] = self.server.ask_path is not None
            bridge = self.server.bridge
            state["clock"] = self.server.clocks.view(bridge.viewer, bridge.game_id)
            self._send(200, "application/json", json.dumps(state).encode("utf-8"))
            return
        if path == "/pgn":
            bridge = self.server.bridge
            self._send_json(*export.raw_pgn(bridge.viewer, bridge.game_id))
            return
        if path == "/games" and self.server.session is not None:
            status, body = self.server.session.games()
            self._send(status, "application/json", json.dumps(body).encode("utf-8"))
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
        """``POST /move`` ``{"uci": ...}`` : the move goes through the bridge's checks.

        ``POST /open`` ``{"game_id": ...}`` : see :meth:`.games.Session.open_game`.

        ``POST /save-pgn`` ``{"game_id": ...}`` : the game on screen, see
        :func:`.export.save_pgn`.
        """
        if self.headers.get("Host") not in self.server.allowed_hosts():
            self._send(421, "text/plain", b"wrong host")
            return
        session = self.server.session
        allowed = {"/move"}
        if session is not None:
            allowed.add("/open")
        if self.server.ask_path is not None:
            allowed.add("/save-pgn")
        if self.path not in allowed:
            self._send(404, "text/plain", b"not found")
            return
        if self.headers.get("Origin") not in self.server.allowed_origins():
            self._answer(403, "origine refusée")
            return
        nonce = self.headers.get("X-Palamede-Nonce", "").encode("latin-1")
        if not hmac.compare_digest(nonce, self.server.nonce.encode("ascii")):
            self._answer(403, "nonce refusé")
            return
        field = "uci" if self.path == "/move" else "game_id"
        value = self._read_field(field)
        if value is None:
            return
        if self.path == "/save-pgn":
            self._save_pgn(value)
            return
        if session is not None and self.path == "/open":
            status, message = session.open_game(value)
        else:
            status, message = play_from_page(self.server.bridge, value)
        self._answer(status, message)

    def _save_pgn(self, game_id: str) -> None:
        bridge = self.server.bridge
        ask_path = self.server.ask_path
        if game_id != bridge.game_id or ask_path is None:
            # Another game opened meanwhile : never save the wrong one.
            self._send_json(409, {"error": "La partie a changé : rouvrez le PGN."})
            return
        if not self.server.saving.acquire(blocking=False):
            self._send_json(409, {"error": "Un enregistrement est déjà en cours."})
            return
        try:
            self._send_json(*export.save_pgn(bridge.viewer, game_id, ask_path))
        finally:
            self.server.saving.release()

    def _read_field(self, field: str) -> str | None:
        """The body's string ``field``, or ``None`` once the error is answered."""
        if self.headers.get_content_type() != "application/json":
            self._answer(415, "JSON attendu")
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._answer(411, "longueur absente")
            return None
        if not 0 < length <= MAX_BODY_BYTES:
            self._answer(413, "corps trop long")
            return None
        try:
            value = json.loads(self.rfile.read(length))[field]
        except (ValueError, KeyError, TypeError):
            value = None
        if not isinstance(value, str):
            self._answer(400, "coup illisible" if field == "uci" else "partie illisible")
            return None
        return value

    def _answer(self, status: int, message: str) -> None:
        body = {"ok": True} if status == 200 else {"error": message}
        self._send(status, "application/json", json.dumps(body).encode("utf-8"))

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
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
