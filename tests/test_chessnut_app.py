"""« Échiquier » app (#78, part 1) : state shown to the page, local page server."""

from __future__ import annotations

import http.client
import json
import logging
import sys
import threading
import types
from pathlib import Path

import chess
import pytest
from palamede_chessnut import app as app_module
from palamede_chessnut import bridge as bridge_module
from palamede_chessnut.bridge import Bridge, cancel_on_sigterm
from palamede_chessnut.cli import bridge_from_args
from palamede_chessnut.page import CHESSGROUND, CONTENT_SECURITY_POLICY, PageServer
from palamede_chessnut.status import RecentLog, captured, setup_steps, snapshot, sync_light

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
    assert state["lights"] == {
        "bluetooth": "searching",
        "server": "connecting",
        "sync": "waiting",
        "token": "checking",
    }
    assert state["account"] is None


def test_snapshot_shows_the_squares_to_fix_and_the_moves() -> None:
    bridge = bridge_on(e4())
    bridge.observed = chess.BaseBoard()  # the board still shows the start
    state = snapshot(bridge)
    assert state["fen"] == e4().fen()
    assert state["history"] == ["e4"]
    assert state["differing"] == ["e2", "e4"]
    assert state["setup"] == {
        "put": [{"symbol": "♙\ufe0e", "colour": "white", "square": "e4"}],
        "remove": [{"square": "e2"}],
    }
    assert state["lights"]["sync"] == "fixing"


@pytest.mark.parametrize(
    ("fen", "uci", "text"),
    [
        (chess.STARTING_FEN, "g1f3", "cavalier g1 → f3"),
        (chess.STARTING_FEN, "e2e4", "pion e2 → e4"),
        (
            "r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1",
            "e8g8",
            "petit roque, roi e8 → g8 et tour h8 → f8",
        ),
        (
            "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
            "e1c1",
            "grand roque, roi e1 → c1 et tour a1 → d1",
        ),
        ("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e7e8q", "pion e7 → e8, promu en dame"),
        ("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e7e8n", "pion e7 → e8, promu en cavalier"),
        (
            "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2",
            "e5d6",
            "pion e5 → d6, en passant : retirez le pion d5",
        ),
    ],
    ids=["piece", "pawn", "short castling", "long castling", "queen", "knight", "en passant"],
)
def test_the_move_to_relay_is_said_plainly(fen: str, uci: str, text: str) -> None:
    """Review of Stéphane (28/10) and Junior (#109) : a plain sentence, special moves whole."""
    before = chess.Board(fen)
    move = chess.Move.from_uci(uci)
    san = before.san(move)
    after = before.copy()
    after.push(move)
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    bridge.relay_from, bridge.game, bridge.to_relay = before, after, f"{uci} ({san})"
    assert snapshot(bridge)["relay_text"] == f"Jouez le coup de l'adversaire : {text}"


def test_relay_without_its_position_shows_the_san() -> None:
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    bridge.to_relay = "Nf6"
    assert snapshot(bridge)["relay_text"] == "Jouez le coup de l'adversaire : Nf6"


def test_relay_keeps_the_position_it_was_played_from() -> None:
    """Through the stream, as on the board : the bridge remembers the position before."""
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    bridge.apply_stream(stream(chess.Board()))
    bridge.ready, bridge.observed = True, chess.BaseBoard()
    game = chess.Board()
    game.push_san("Nf3")
    bridge.apply_stream(stream(game))
    assert bridge.to_relay == "g1f3 (Nf3)"
    assert snapshot(bridge)["relay_text"] == "Jouez le coup de l'adversaire : cavalier g1 → f3"


def test_no_move_to_relay_no_text() -> None:
    assert snapshot(Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN))["relay_text"] is None


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
    bridge.stream_link = "reconnecting"
    assert snapshot(bridge)["lights"]["server"] == "reconnecting"


def test_first_ply_follows_the_fen() -> None:
    """008d (28/10) : set up with Black to move at 29, not a game from « 1. »."""
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    bridge.apply_stream(
        {
            "fen": "6k1/5pp1/4p2p/b3P3/2Q5/2p1P1KP/5PP1/8 w - - 2 33",
            "ply": 64,
            "turn": "white",
            "san_history": ["c3", "Qxd3", "Bb4", "Kg3", "h6", "Qc4", "Ba5"],
            "is_game_over": False,
        }
    )
    state = snapshot(bridge)
    assert state["first_ply"] == 57  # 29... c3 : half-move 57, Black's 29th
    assert state["first_ply"] % 2 == 1 and state["first_ply"] // 2 + 1 == 29


def test_first_ply_of_an_ordinary_game_is_zero() -> None:
    bridge = bridge_on(e4())
    assert snapshot(bridge)["first_ply"] == 0


def test_setup_says_which_piece_goes_where() -> None:
    """Review of Stéphane (28/10) : piece symbols and colours, by colour then type (#117)."""
    game = chess.BaseBoard("6k1/5pp1/4p2p/b3P3/2Q5/2p1P1KP/5PP1/8")
    observed = chess.BaseBoard("6k1/1p3pp1/3p3p/4P3/1P2P3/4P1KP/5PPP/5B2")
    steps = setup_steps(game, observed)
    put = [(p["symbol"].rstrip("\ufe0e"), p["colour"], p["square"]) for p in steps["put"]]
    assert put == [
        ("♕", "white", "c4"),
        ("♝", "black", "a5"),
        ("♟", "black", "e6"),
        ("♟", "black", "c3"),
    ]
    assert all(p["symbol"].endswith("\ufe0e") for p in steps["put"])
    assert [r["square"] for r in steps["remove"]] == ["b7", "d6", "b4", "e4", "h2", "f1"]


def test_setup_names_a_wrong_piece_by_what_belongs_there() -> None:
    """A knight where a bishop belongs : the knight to remove, the bishop to put (Admin)."""
    game = chess.BaseBoard()
    observed = chess.BaseBoard()
    observed.set_piece_at(chess.C1, chess.Piece(chess.KNIGHT, chess.WHITE))
    assert setup_steps(game, observed) == {
        "put": [{"symbol": "♗\ufe0e", "colour": "white", "square": "c1"}],
        "remove": [{"square": "c1"}],
    }


def test_setup_puts_by_colour_then_type_then_rank() -> None:
    """#117 (Stéphane, 29/10) : White then Black, each king to pawn, top rank first."""
    game = chess.BaseBoard("r2qk3/pp6/8/8/8/8/PP6/R3K2N")
    steps = setup_steps(game, chess.BaseBoard("8/8/8/8/8/8/8/8"))
    put = [(p["symbol"].rstrip("\ufe0e"), p["square"]) for p in steps["put"]]
    assert put == [
        ("♔", "e1"),
        ("♖", "a1"),
        ("♘", "h1"),
        ("♙", "a2"),
        ("♙", "b2"),
        ("♚", "e8"),
        ("♛", "d8"),
        ("♜", "a8"),
        ("♟", "a7"),
        ("♟", "b7"),
    ]
    assert steps["remove"] == []


def test_nothing_to_set_up() -> None:
    assert setup_steps(chess.BaseBoard(), chess.BaseBoard()) == {"put": [], "remove": []}


def _plain(symbols: list[str]) -> str:
    return "".join(symbol.rstrip("\ufe0e") for symbol in symbols)


def test_nothing_captured_at_the_start() -> None:
    assert captured(chess.BaseBoard()) == {"by_white": [], "by_black": [], "balance": 0}


def test_captured_strongest_first_with_the_balance() -> None:
    """#117 : what each side has taken, queen to pawn, and the material balance."""
    board = chess.Board()
    for san in ["e4", "d5", "exd5", "Nf6", "Bb5+", "c6", "dxc6", "Qb6", "cxb7+", "Nbd7", "bxa8=Q"]:
        board.push_san(san)
    # Black lost the a8 rook and three pawns (d5, c6, b7) ; the a8 queen was the e-pawn.
    taken = captured(chess.BaseBoard(board.board_fen()))
    assert _plain(taken["by_white"]) == "♜♟♟♟"
    assert taken["by_black"] == []
    assert taken["balance"] == 5 + 3 + 9 - 1
    assert all(symbol.endswith("\ufe0e") for symbol in taken["by_white"])


def test_the_queen_before_the_rook() -> None:
    """Queen and rook both taken : the queen first (mutant of Junior, #146)."""
    board = chess.BaseBoard()
    board.remove_piece_at(chess.A8)
    board.remove_piece_at(chess.D8)
    taken = captured(board)
    assert _plain(taken["by_white"]) == "♛♜"
    assert taken["balance"] == 9 + 5


def test_a_promoted_piece_is_the_pawn_not_taken() -> None:
    """A second white queen and no a-pawn : the pawn was promoted, not taken."""
    board = chess.BaseBoard()
    board.remove_piece_at(chess.A2)
    board.set_piece_at(chess.A6, chess.Piece(chess.QUEEN, chess.WHITE))
    board.remove_piece_at(chess.A7)
    taken = captured(board)
    assert taken["by_black"] == []
    assert _plain(taken["by_white"]) == "♟"
    assert taken["balance"] == 9 + 1 - 1


def test_black_ahead_gives_a_negative_balance() -> None:
    board = chess.BaseBoard()
    board.remove_piece_at(chess.D1)
    board.remove_piece_at(chess.B8)
    taken = captured(board)
    assert _plain(taken["by_black"]) == "♕"
    assert _plain(taken["by_white"]) == "♞"
    assert taken["balance"] == 3 - 9


def test_snapshot_gives_the_captured_pieces() -> None:
    assert snapshot(bridge_on(e4()))["captured"] == {"by_white": [], "by_black": [], "balance": 0}


def test_board_light_is_grey_once_the_bridge_stops() -> None:
    """Game over, the bridge has closed the link : grey, not orange (Mac smoke, 28/10)."""
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    bridge.board_link = "searching"
    assert snapshot(bridge)["lights"]["bluetooth"] == "searching"
    bridge.finished = True
    bridge.stopped.set()
    assert snapshot(bridge)["lights"]["bluetooth"] == "closed"


def test_board_light_after_the_end_without_a_board_or_while_connected() -> None:
    """Review Junior #113 : --no-board keeps no light ; connected then stopped is grey."""
    mouse = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN, with_board=False)
    mouse.finished = True
    mouse.stopped.set()
    assert snapshot(mouse)["lights"]["bluetooth"] == "none"
    board = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    board.board_link, board.link_kind = "connected", "usb"
    board.finished = True
    board.stopped.set()
    assert snapshot(board)["lights"]["bluetooth"] == "closed"


def test_board_light_is_red_when_the_bridge_stops_on_an_error() -> None:
    """Review Admin #113 : a stream error or a redirect also stops the bridge : red."""
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    bridge.stopped.set()
    assert not bridge.finished
    assert snapshot(bridge)["lights"]["bluetooth"] == "failing"


@pytest.mark.parametrize(
    ("transport", "kind", "link", "light"),
    [
        ("auto", "ble", "connected", "ble"),
        ("auto", "usb", "connected", "usb"),
        ("ble", None, "connected", "ble"),
        ("usb", None, "connected", "usb"),
        ("auto", None, "searching", "searching"),
        ("auto", "usb", "failing", "failing"),
    ],
)
def test_board_light_says_the_link_and_its_trouble(transport, kind, link, light) -> None:
    """Review of Stéphane (28/10) : blue Bluetooth, green USB, orange between, red on errors."""
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN, board_transport=transport)
    bridge.link_kind, bridge.board_link = kind, link
    assert snapshot(bridge)["lights"]["bluetooth"] == light


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
    assert response.getheader("X-Content-Type-Options") == "nosniff"
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
    """The bridge ships its own copy (GPL-3.0 : licence, provenance, ``src/`` included).

    It must not drift from the viewer's, file for file (review Admin #87).
    """

    def tree(root: Path) -> dict[str, bytes]:
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts
        }

    copy, viewer = tree(CHESSGROUND), tree(VIEWER_VENDOR)
    assert {"LICENSE", "README.md", "package.json", "chessground.min.js"} <= set(copy)
    assert any(name.startswith("src") for name in copy)
    assert copy == viewer


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


# ---- link lights on a dead link (review Junior #87) ------------------------


def test_server_light_goes_orange_when_the_stream_drops(monkeypatch) -> None:
    def unreachable(*_args, **_kwargs):
        raise OSError("connection refused")

    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    bridge.stream_link = "connected"
    monkeypatch.setattr(bridge_module.urllib.request, "urlopen", unreachable)
    monkeypatch.setattr(bridge_module.time, "sleep", lambda _s: bridge.stopped.set())
    bridge._follow_stream(loop=None)  # type: ignore[arg-type]
    assert bridge.stream_link == "reconnecting"
    assert snapshot(bridge)["lights"]["server"] == "reconnecting"


def test_bluetooth_light_goes_orange_when_the_board_drops(monkeypatch) -> None:
    """Connected while the link holds, back to « searching » once it is gone."""
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN, board_transport="ble")
    seen: list[str] = []

    class Client:
        is_connected = True

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> bool:
            return False

        async def start_notify(self, *_args) -> None:
            pass

        async def write_gatt_char(self, *_args) -> None:
            pass

    class Scanner:
        @staticmethod
        async def find_device_by_filter(*_args, **_kwargs):
            return types.SimpleNamespace(name="Chessnut Air")

    async def tick(self, _write) -> None:
        seen.append(self.board_link)
        self.stopped.set()  # the link « drops » after one tick

    fake_bleak = types.ModuleType("bleak")
    fake_bleak.BleakClient = Client  # type: ignore[attr-defined]
    fake_bleak.BleakScanner = Scanner  # type: ignore[attr-defined]
    fake_exc = types.ModuleType("bleak.exc")
    fake_exc.BleakError = type("BleakError", (Exception,), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bleak", fake_bleak)
    monkeypatch.setitem(sys.modules, "bleak.exc", fake_exc)
    monkeypatch.setattr(Bridge, "tick", tick)
    bridge_module.asyncio.run(bridge.run_board())
    assert seen == ["connected"]
    assert bridge.board_link == "searching"


# ---- closing the window, or failing to open it ----------------------------


@pytest.fixture
def window_app(monkeypatch):
    """``app.main`` with a fake pywebview, a bridge thread that waits, a recorded page."""
    monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", TOKEN)
    pages: list[PageServer] = []

    class RecordedPage(PageServer):
        closed = False

        def __init__(self, *args) -> None:
            super().__init__(*args)
            pages.append(self)

        def server_close(self) -> None:
            super().server_close()
            self.closed = True

    bridges: list[Bridge] = []

    def waiting_bridge(bridge: Bridge) -> None:
        bridges.append(bridge)
        bridge.stopped.wait(5)

    monkeypatch.setattr(app_module, "PageServer", RecordedPage)
    monkeypatch.setattr(app_module, "run_bridge", waiting_bridge)
    webview = types.ModuleType("webview")
    webview.create_window = lambda *_a, **_k: None  # type: ignore[attr-defined]
    menu = types.ModuleType("webview.menu")
    menu.Menu = lambda title, items: (title, items)  # type: ignore[attr-defined]
    menu.MenuAction = lambda title, function: (title, function)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setitem(sys.modules, "webview.menu", menu)
    argv = ["--viewer", "https://viewer.test", "--game", "g1", "--color", "white"]
    return webview, argv, pages, bridges


def test_the_window_allows_text_selection(window_app) -> None:
    """The FEN and the journal can be selected : pywebview disables it by default."""
    webview, argv, _pages, _bridges = window_app
    seen: dict = {}
    webview.create_window = lambda *_a, **kw: seen.update(kw)  # type: ignore[attr-defined]
    webview.start = lambda **_k: None
    app_module.main(argv)
    assert seen["text_select"] is True


def test_closing_the_window_stops_the_bridge(window_app) -> None:
    webview, argv, pages, bridges = window_app
    webview.start = lambda **_k: None
    app_module.main(argv)
    assert bridges[0].stopped.is_set()
    assert pages[0].closed


def test_a_window_that_cannot_open_stops_the_bridge(window_app) -> None:
    """No graphical session : pywebview raises, the LEDs must still go off."""
    webview, argv, pages, bridges = window_app

    def no_screen(**_k) -> None:
        raise AttributeError("'NoneType' object has no attribute 'frame'")

    webview.start = no_screen
    with pytest.raises(AttributeError):
        app_module.main(argv)
    assert bridges[0].stopped.is_set()
    assert pages[0].closed
