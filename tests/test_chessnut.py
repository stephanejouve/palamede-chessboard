"""Chessnut Air+ bridge : wire format, move reading, bridge decisions.

The ``REAL_*`` frames were captured on an Air+ : on 2026-10-23 white pieces
only on the board, then 1.d4 played and taken back ; on 2026-10-24 the final
position of the first real game (``chessnut-stephane-001``), which holds all
six black piece types.
"""

from __future__ import annotations

import http.server
import io
import threading
import time
import urllib.error

import chess
import pytest
from palamede_chessnut import bridge as bridge_module
from palamede_chessnut import viewer as viewer_module
from palamede_chessnut.bridge import (
    GUIDE_SECONDS,
    RESIGN_SECONDS,
    Bridge,
    decide,
)
from palamede_chessnut.cli import check_viewer_url, read_token
from palamede_chessnut.moves import differing_squares, find_move
from palamede_chessnut.protocol import (
    PIECE_CODES,
    UnknownPieceCode,
    decode_board,
    led_frame,
)

REAL_WHITE_ONLY = bytes.fromhex("0124a6b99c6a77777777" + "00" * 24 + "15000000")
REAL_D4 = bytes.fromhex("0124a6b99c6a77077777" + "0000000000700000" + "00" * 16 + "19000000")
REAL_MATE = bytes.fromhex(
    "0124a609000677b000c0007770100000500400030400000500004404400408008002c5050000"
)
CODES = {symbol: code for code, symbol in PIECE_CODES.items()}


def frame(board: chess.BaseBoard) -> bytes:
    """Board frame for ``board`` (inverse of :func:`decode_board`)."""
    placement = bytearray(32)
    for square, piece in board.piece_map().items():
        rank, file = chess.square_rank(square), chess.square_file(square)
        shift = 4 * (file % 2)
        placement[4 * rank + file // 2] |= CODES[piece.symbol()] << shift
    return bytes([0x01, 0x24]) + bytes(placement) + bytes(4)


def after(*sans: str, fen: str = chess.STARTING_FEN) -> chess.Board:
    board = chess.Board(fen)
    for san in sans:
        board.push_san(san)
    return board


def placement(board: chess.Board) -> chess.BaseBoard:
    return chess.BaseBoard(board.board_fen())


# ---- protocol --------------------------------------------------------------


def test_real_frames_decode() -> None:
    assert decode_board(REAL_WHITE_ONLY).board_fen() == "8/8/8/8/8/8/PPPPPPPP/RNBQKBNR"
    assert decode_board(REAL_D4).board_fen() == "8/8/8/8/3P4/8/PPP1PPPP/RNBQKBNR"
    mate = after(
        *"e4 e5 d3 Nc6 Nf3 Nf6 Be2 Bc5 O-O O-O h3 d5 exd5 Nxd5 Qd2 Nf4 Bd1 Qd6"
        " c3 Bxh3 Ng5 Qg6 gxh3 Qxg5+ Bg4 h5 Kh2 hxg4 Rg1 Qh4 f3 Qxh3#".split()
    )
    assert decode_board(REAL_MATE).board_fen() == mate.board_fen()


def test_start_position_round_trips() -> None:
    start = chess.BaseBoard()
    assert decode_board(frame(start)).board_fen() == start.board_fen()


def test_other_frames_are_ignored() -> None:
    assert decode_board(bytes([0x23, 0x01, 0x00])) is None
    assert decode_board(REAL_WHITE_ONLY[:20]) is None


def test_unknown_nibble_is_reported() -> None:
    with pytest.raises(UnknownPieceCode, match="a1"):
        decode_board(bytes([0x01, 0x24, 0x0F]) + bytes(35))


def test_led_frames_match_the_probe() -> None:
    # Probe of 2026-10-23 : byte 0 bit 0x01 → a1, bit 0x80 → h1, byte 7 bit 0x01 → a8.
    assert led_frame([chess.A1]) == bytes.fromhex("0a080100000000000000")
    assert led_frame([chess.H1]) == bytes.fromhex("0a088000000000000000")
    assert led_frame([chess.A8]) == bytes.fromhex("0a080000000000000001")
    assert led_frame([chess.E2, chess.E4]) == bytes.fromhex("0a080010001000000000")
    assert led_frame([]) == bytes.fromhex("0a080000000000000000")


# ---- moves -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("fen", "san"),
    [
        (chess.STARTING_FEN, "e4"),
        (after("e4", "d5").fen(), "exd5"),
        (after("e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6").fen(), "O-O"),
        (after("e4", "a6", "e5", "d5").fen(), "exd6"),
        ("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e8=Q"),
        ("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e8=N"),
    ],
)
def test_the_placement_decides_the_move(fen: str, san: str) -> None:
    game = chess.Board(fen)
    move = game.parse_san(san)
    assert find_move(game, placement(after(san, fen=fen))) == move


def test_a_lifted_piece_is_not_a_move() -> None:
    lifted = chess.BaseBoard()
    lifted.remove_piece_at(chess.G1)
    assert find_move(chess.Board(), lifted) is None


def test_differing_squares_point_at_the_move() -> None:
    assert differing_squares(chess.BaseBoard(), placement(after("e4"))) == {chess.E2, chess.E4}


# ---- decisions -------------------------------------------------------------


def test_nothing_happens_before_the_placement_settles() -> None:
    assert decide(chess.Board(), chess.WHITE, placement(after("e4")), 0.1) is None


def test_same_placement_switches_leds_off() -> None:
    assert decide(chess.Board(), chess.WHITE, chess.BaseBoard(), 1.0).leds == frozenset()


def test_own_move_is_posted() -> None:
    decision = decide(chess.Board(), chess.WHITE, placement(after("e4")), 1.0)
    assert decision.move == chess.Move.from_uci("e2e4")
    assert decision.leds == frozenset()


def test_held_piece_lights_nothing_until_the_guide_delay() -> None:
    lifted = chess.BaseBoard()
    lifted.remove_piece_at(chess.G1)
    assert decide(chess.Board(), chess.WHITE, lifted, 1.0).leds == frozenset()
    assert decide(chess.Board(), chess.WHITE, lifted, GUIDE_SECONDS).leds == {chess.G1}


def test_opponent_move_lights_up_until_replicated() -> None:
    game = after("e4", "e5")
    decision = decide(game, chess.WHITE, placement(after("e4")), 1.0, synced=False)
    assert decision.move is None
    assert decision.leds == {chess.E7, chess.E5}
    # Pawn lifted from e7, still in hand : e5 stays lit, no guide delay.
    in_hand = placement(after("e4"))
    in_hand.remove_piece_at(chess.E7)
    assert decide(game, chess.WHITE, in_hand, 1.0, synced=False).leds == {chess.E5}


def test_pending_move_is_not_lit_nor_reposted() -> None:
    decision = decide(chess.Board(), chess.WHITE, placement(after("e4")), 5.0, pending=True)
    assert decision == decide(chess.Board(), chess.WHITE, chess.BaseBoard(), 5.0)


def test_refused_move_lights_up_instead_of_being_reposted() -> None:
    refused = chess.Move.from_uci("e2e4")
    decision = decide(chess.Board(), chess.WHITE, placement(after("e4")), 1.0, refused=refused)
    assert decision.move is None
    assert decision.leds == {chess.E2, chess.E4}


def test_no_move_is_posted_once_the_game_is_over() -> None:
    decision = decide(chess.Board(), chess.WHITE, placement(after("e4")), 1.0, live=False)
    assert decision.move is None


# ---- bridge ----------------------------------------------------------------


def snapshot(board: chess.Board, *, over: bool = False) -> dict:
    turn = "white" if board.turn == chess.WHITE else "black"
    replay = board.root()
    history = [replay.san_and_push(move) for move in board.move_stack]
    return {
        "fen": board.fen(),
        "ply": board.ply(),
        "turn": turn,
        "san_history": history,
        "is_game_over": over,
    }


def ready_bridge(game: chess.Board | None = None, player: chess.Color = chess.WHITE) -> Bridge:
    """A bridge whose board has already matched ``game`` (the ready sweep is behind it)."""
    bridge = Bridge("https://viewer.test", "g1", player, token="t")
    bridge.apply_stream(snapshot(game or chess.Board()))
    bridge.synced = bridge.ready = True
    return bridge


async def test_tick_posts_the_move_and_lights_the_reply() -> None:
    bridge = ready_bridge()
    posted: list[str] = []

    def accept(uci: str) -> tuple[None, bool]:
        posted.append(uci)
        return None, False

    bridge._post_move = accept  # type: ignore[method-assign]
    written: list[bytes] = []

    async def write(command: bytes) -> None:
        written.append(command)

    bridge.on_frame(None, bytearray(frame(placement(after("e4")))))
    bridge.settled_since = time.monotonic() - 1.0
    await bridge.tick(write)
    assert posted == ["e2e4"]
    assert written == [led_frame([])]

    bridge.apply_stream(snapshot(after("e4", "e5")))
    await bridge.tick(write)
    assert written[-1] == led_frame([chess.E7, chess.E5])


async def test_refused_move_is_not_posted_twice() -> None:
    bridge = ready_bridge()
    posted: list[str] = []

    def refuse(uci: str) -> tuple[str, bool]:
        posted.append(uci)
        return "HTTP 403 NOT_YOUR_TURN", True

    bridge._post_move = refuse  # type: ignore[method-assign]

    async def write(command: bytes) -> None:
        pass

    bridge.on_frame(None, bytearray(frame(placement(after("e4")))))
    bridge.settled_since = time.monotonic() - 1.0
    await bridge.tick(write)
    await bridge.tick(write)
    assert posted == ["e2e4"]
    assert bridge.leds == {chess.E2, chess.E4}


def test_glitched_frame_is_dropped() -> None:
    bridge = Bridge("http://viewer.test", "g1", chess.WHITE, token="t")
    bridge.on_frame(None, bytearray([0x01, 0x24, 0x0F]) + bytearray(35))
    assert bridge.observed is None


def test_reconnection_snapshot_keeps_the_pending_move() -> None:
    bridge = Bridge("http://viewer.test", "g1", chess.WHITE, token="t")
    bridge.apply_stream(snapshot(chess.Board()))
    bridge.pending = chess.Move.from_uci("e2e4")
    bridge.apply_stream(snapshot(chess.Board()))
    assert bridge.pending is not None
    bridge.apply_stream(snapshot(after("e4")))
    assert bridge.pending is None


async def test_undelivered_move_is_retried_not_refused() -> None:
    """Review #71 : a network error says nothing about the move."""
    bridge = ready_bridge()
    posted: list[str] = []

    def unreachable(uci: str) -> tuple[str, bool]:
        posted.append(uci)
        return "[Errno 65] No route to host", False

    bridge._post_move = unreachable  # type: ignore[method-assign]
    bridge._server_position = lambda: (chess.STARTING_FEN, False)  # type: ignore[method-assign]
    written: list[bytes] = []

    async def write(command: bytes) -> None:
        written.append(command)

    bridge.on_frame(None, bytearray(frame(placement(after("e4")))))
    bridge.settled_since = time.monotonic() - 1.0
    await bridge.tick(write)
    await bridge.tick(write)
    assert posted == ["e2e4"]
    assert bridge.refused is None
    assert written == [led_frame([])]
    bridge.retry_at = 0.0
    await bridge.tick(write)
    assert posted == ["e2e4", "e2e4"]


def test_token_is_not_forwarded_on_a_redirect() -> None:
    """A 302 from the viewer must not carry the player token elsewhere (review Admin #71)."""
    seen: dict[str, str | None] = {}

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            seen["authorization"] = self.headers.get("Authorization")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    target = http.server.HTTPServer(("127.0.0.1", 0), Target)

    class Viewer(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            seen["first"] = self.headers.get("Authorization")
            # Read the body : closing on unread data resets the connection.
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/elsewhere")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    viewer = http.server.HTTPServer(("127.0.0.1", 0), Viewer)
    for server in (target, viewer):
        threading.Thread(target=server.handle_request, daemon=True).start()
    try:
        bridge = Bridge(f"http://127.0.0.1:{viewer.server_port}", "g1", chess.WHITE, "secret")
        message, refused = bridge._post_move("e2e4")
    finally:
        target.server_close()
        viewer.server_close()
    assert seen["first"] == "Bearer secret"
    assert seen.get("authorization") is None
    # The redirect is not followed : a wrong --viewer stops the bridge.
    assert message and not refused
    assert bridge.stopped.is_set()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (urllib.error.HTTPError("u", 422, "Unprocessable", {}, io.BytesIO(b"illegal")), True),
        (urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b"NOT_YOUR_TURN")), True),
        (urllib.error.HTTPError("u", 503, "Unavailable", {}, io.BytesIO(b"")), False),
        (urllib.error.URLError("unreachable"), False),
        (TimeoutError("timed out"), False),
    ],
)
def test_only_a_4xx_is_a_refusal(monkeypatch, error: Exception, expected: bool) -> None:
    class Opener:
        def open(self, *_args, **_kwargs):
            raise error

    monkeypatch.setattr(viewer_module, "_OPENER", Opener())
    message, refused = Bridge("https://viewer.test", "g1", chess.WHITE, "t")._post_move("e2e4")
    assert message and refused is expected


def test_stream_error_stops_the_bridge(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return iter([b'data: {"error": "GAME_NOT_FOUND", "game_id": "g1"}\n'])

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(bridge_module.urllib.request, "urlopen", lambda *_a, **_k: Response())

    class Loop:
        def __init__(self) -> None:
            self.calls: list = []

        def call_soon_threadsafe(self, callback, *args) -> None:
            self.calls.append((callback, *args))

    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, "t")
    loop = Loop()
    bridge._follow_stream(loop=loop)  # type: ignore[arg-type]
    assert bridge.stopped.is_set()
    assert loop.calls == [(bridge.stream_opened, False)]


@pytest.mark.parametrize(
    "url", ["https://chess.example", "http://localhost:8765", "http://127.0.0.1:8765"]
)
def test_viewer_url_accepted(url: str) -> None:
    check_viewer_url(url)


@pytest.mark.parametrize("url", ["http://192.168.1.10:8765", "http://chess.example", "ftp://x"])
def test_viewer_url_that_would_leak_the_token_is_refused(url: str) -> None:
    with pytest.raises(SystemExit, match="https required"):
        check_viewer_url(url)


def test_token_file_must_be_private(tmp_path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret\n", encoding="utf-8")
    token_file.chmod(0o644)
    with pytest.raises(SystemExit, match="chmod 600"):
        read_token(token_file)
    token_file.chmod(0o600)
    assert read_token(token_file) == "secret"


async def _retry_after_network_error(server_fen: str | None) -> tuple[Bridge, list[str]]:
    """1.e4 on the board, first POST lost, then the retry with ``server_fen`` read back."""
    bridge = ready_bridge()
    posted: list[str] = []

    def lost(uci: str) -> tuple[str, bool]:
        posted.append(uci)
        return "timed out", False

    bridge._post_move = lost  # type: ignore[method-assign]
    bridge._server_position = lambda: (  # type: ignore[method-assign]
        None if server_fen is None else (server_fen, False)
    )

    async def write(command: bytes) -> None:
        pass

    bridge.on_frame(None, bytearray(frame(placement(after("e4")))))
    bridge.settled_since = time.monotonic() - 1.0
    await bridge.tick(write)
    bridge.retry_at = 0.0
    await bridge.tick(write)
    return bridge, posted


async def test_move_that_reached_the_viewer_is_not_posted_twice() -> None:
    """Review Admin #71 : the lost POST had landed, the retry must not refuse it."""
    bridge, posted = await _retry_after_network_error(after("e4").fen())
    assert posted == ["e2e4"]
    assert bridge.game.fen() == after("e4").fen()
    assert bridge.refused is None


async def test_move_that_did_not_reach_the_viewer_is_posted_again() -> None:
    _bridge, posted = await _retry_after_network_error(chess.Board().fen())
    assert posted == ["e2e4", "e2e4"]


async def test_retry_waits_while_the_viewer_cannot_be_read() -> None:
    bridge, posted = await _retry_after_network_error(None)
    assert posted == ["e2e4"]
    assert bridge.retry_at > time.monotonic()


# ---- ready, resignation, end of game ------------------------------------------


def test_setup_placement_is_not_read_as_a_move_before_the_board_matched() -> None:
    """A pawn put on e4 instead of e2 while setting up is not 1.e4."""
    decision = decide(chess.Board(), chess.WHITE, placement(after("e4")), 5.0, synced=False)
    assert decision.move is None
    assert decision.leds == {chess.E2, chess.E4}


class Board:
    """Writes captured as LED square sets."""

    def __init__(self) -> None:
        self.frames: list[set[chess.Square]] = []

    async def write(self, command: bytes) -> None:
        ranks = command[2:]
        self.frames.append(
            {chess.square(f, r) for r in range(8) for f in range(8) if ranks[r] >> f & 1}
        )


async def test_first_match_plays_the_ready_sweep_then_goes_dark() -> None:
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, token="t")
    board = Board()
    bridge.apply_stream(snapshot(chess.Board()))
    bridge.on_frame(None, bytearray(frame(chess.BaseBoard())))
    for _ in range(12):
        await bridge.tick(board.write)
    assert bridge.ready
    assert board.frames[:8] == [{chess.square(f, r) for f in range(8)} for r in range(8)]
    assert board.frames[-1] == set()


async def test_first_match_logs_the_fen(caplog: pytest.LogCaptureFixture) -> None:
    """Once the board matches, the FEN is in the log : a position set from a problem can be checked."""
    game = chess.Board("4r2k/pp4pp/2n4N/q7/2Q5/8/PPP2PPP/3R2K1 w - - 0 1")
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, token="t")
    bridge.apply_stream(snapshot(game))
    bridge.on_frame(None, bytearray(frame(placement(game))))
    with caplog.at_level("INFO", logger=bridge_module.log.name):
        await bridge.tick(Board().write)
    assert f"FEN {game.fen()}" in caplog.messages


async def test_relayed_move_is_logged_once_played(caplog: pytest.LogCaptureFixture) -> None:
    """The opponent's move shows in the log when it stands on the board, not before (game 006)."""
    bridge = ready_bridge(after("e4"))
    bridge.on_frame(None, bytearray(frame(placement(after("e4")))))
    board = Board()
    with caplog.at_level("INFO", logger=bridge_module.log.name):
        bridge.apply_stream(snapshot(after("e4", "e5")))
        await bridge.tick(board.write)
        assert "move e7e5 (e5) played on the board" not in caplog.messages
        bridge.on_frame(None, bytearray(frame(placement(after("e4", "e5")))))
        await bridge.tick(board.write)
        await bridge.tick(board.write)
    assert caplog.messages.count("move e7e5 (e5) played on the board") == 1


async def test_own_posted_move_is_not_relayed(caplog: pytest.LogCaptureFixture) -> None:
    """The player's move read on the board comes back from the viewer : nothing to relay."""
    bridge = ready_bridge()
    bridge.on_frame(None, bytearray(frame(placement(after("e4")))))
    with caplog.at_level("INFO", logger=bridge_module.log.name):
        bridge.apply_stream(snapshot(after("e4")))
        await bridge.tick(Board().write)
    assert not [m for m in caplog.messages if m.endswith("played on the board")]


async def test_move_played_in_the_viewer_is_relayed(caplog: pytest.LogCaptureFixture) -> None:
    """Mixed game : the player's own move entered in the viewer is relayed too (chessnut-abandon-001)."""
    bridge = ready_bridge()
    bridge.on_frame(None, bytearray(frame(chess.BaseBoard())))
    with caplog.at_level("INFO", logger=bridge_module.log.name):
        bridge.apply_stream(snapshot(after("Nc3")))
        bridge.on_frame(None, bytearray(frame(placement(after("Nc3")))))
        await bridge.tick(Board().write)
    assert "move b1c3 (Nc3) played on the board" in caplog.messages


async def test_setup_relays_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """A game joined in progress : setting it up is not a relay, only the FEN is logged."""
    game = after("e4", "e5")
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, token="t")
    bridge.apply_stream(snapshot(game))
    bridge.on_frame(None, bytearray(frame(placement(game))))
    with caplog.at_level("INFO", logger=bridge_module.log.name):
        await bridge.tick(Board().write)
    assert f"FEN {game.fen()}" in caplog.messages
    assert not [m for m in caplog.messages if m.endswith("played on the board")]


async def test_mating_move_is_relayed_before_the_end(caplog: pytest.LogCaptureFixture) -> None:
    """The opponent mates : its move is logged, then the game is over."""
    before = after("f3", "e5", "g4")
    mate = after("f3", "e5", "g4", "Qh4#")
    bridge = ready_bridge(before)
    bridge.on_frame(None, bytearray(frame(placement(before))))
    with caplog.at_level("INFO", logger=bridge_module.log.name):
        bridge.apply_stream(snapshot(mate, over=True))
        bridge.on_frame(None, bytearray(frame(placement(mate))))
        await bridge.tick(Board().write)
    played = caplog.messages.index("move d8h4 (Qh4#) played on the board")
    assert played < caplog.messages.index("game g1 over")


async def test_move_read_is_logged_with_its_san(caplog: pytest.LogCaptureFixture) -> None:
    bridge = ready_bridge()
    bridge._post_move = lambda uci: (None, False)  # type: ignore[method-assign]
    bridge.on_frame(None, bytearray(frame(placement(after("Nf3")))))
    bridge.settled_since = time.monotonic() - 1.0
    with caplog.at_level("INFO", logger=bridge_module.log.name):
        await bridge.tick(Board().write)
    assert "move read on the board : g1f3 (Nf3)" in caplog.messages


def test_relayed_move_without_its_position_keeps_the_san() -> None:
    """Several moves missed (stream back after a long loss) : no UCI to rebuild, the SAN stays."""
    assert bridge_module._relayed(chess.Board(), "Nf6", after("e4", "e5", "Nf3", "Nf6")) == "Nf6"
    assert bridge_module._relayed(None, "Nf6", after("Nf3", "Nf6")) == "Nf6"
    assert bridge_module._relayed(after("Nf3"), "Nf6", after("Nf3", "Nf6")) == "g8f6 (Nf6)"


async def test_guidance_cuts_the_ready_sweep() -> None:
    """The opponent moves while the sweep runs : its squares light up at once."""
    bridge = Bridge("https://viewer.test", "g1", chess.BLACK, token="t")
    board = Board()
    bridge.apply_stream(snapshot(chess.Board()))
    bridge.on_frame(None, bytearray(frame(chess.BaseBoard())))
    await bridge.tick(board.write)
    assert bridge.animation
    bridge.apply_stream(snapshot(after("e4")))
    bridge.settled_since = time.monotonic() - 1.0
    await bridge.tick(board.write)
    assert bridge.animation == []
    assert board.frames[-1] == {chess.E2, chess.E4}


def _without_king(board: chess.Board, color: chess.Color) -> chess.BaseBoard:
    lifted = placement(board)
    king = lifted.king(color)
    assert king is not None
    lifted.remove_piece_at(king)
    return lifted


async def _lift_king(
    answer: tuple[int | None, str], *, held_for: float = RESIGN_SECONDS
) -> tuple[Bridge, list[int], Board]:
    bridge = ready_bridge(after("e4"), chess.BLACK)
    calls: list[int] = []

    def resign() -> tuple[int | None, str]:
        calls.append(1)
        return answer

    bridge._post_resign = resign  # type: ignore[method-assign]
    board = Board()
    bridge.on_frame(None, bytearray(frame(_without_king(after("e4"), chess.BLACK))))
    await bridge.tick(board.write)
    bridge.king_gone_since = time.monotonic() - held_for
    await bridge.tick(board.write)
    await bridge.tick(board.write)
    return bridge, calls, board


async def test_king_off_the_board_resigns_once() -> None:
    bridge, calls, board = await _lift_king((200, ""))
    assert calls == [1]
    assert bridge.resign_state == "sent"
    assert all(squares <= {chess.E8} for squares in board.frames)


async def test_resignation_answered_409_counts_as_arrived() -> None:
    """Review Admin #73 : an earlier, unanswered try may have landed."""
    bridge, calls, _board = await _lift_king((409, "already ended"))
    assert calls == [1] and bridge.resign_state == "sent"


async def test_old_viewer_without_resignation_falls_back_to_guidance() -> None:
    bridge, calls, board = await _lift_king((404, "Not Found"))
    assert calls == [1] and bridge.resign_state == "unavailable"
    bridge.settled_since = time.monotonic() - 5.0
    await bridge.tick(board.write)
    assert board.frames[-1] == {chess.E8}


async def test_reconnected_stream_tries_resignation_again() -> None:
    """chessnut-abandon-001 : after the viewer upgrade, the gesture works without a restart."""
    bridge, calls, _board = await _lift_king((404, "Not Found"))
    assert bridge.resign_state == "unavailable"
    bridge.stream_opened()
    assert bridge.resign_state is None
    bridge.stream_opened()
    assert bridge.resign_state is None


def test_lost_stream_reopens_as_back(monkeypatch) -> None:
    """Review Junior #77 : the loop itself passes ``back=True`` after a loss."""

    class Response:
        def __enter__(self):
            return iter([b'data: {"error": "GAME_NOT_FOUND", "game_id": "g1"}\n'])

        def __exit__(self, *_exc):
            return False

    answers: list = [OSError("502 Bad Gateway"), Response()]

    def urlopen(*_a, **_k):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    class Loop:
        def __init__(self) -> None:
            self.calls: list = []

        def call_soon_threadsafe(self, callback, *args) -> None:
            self.calls.append((callback, *args))

    monkeypatch.setattr(bridge_module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bridge_module.time, "sleep", lambda _s: None)
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, "t")
    loop = Loop()
    bridge._follow_stream(loop=loop)  # type: ignore[arg-type]
    assert loop.calls == [(bridge.stream_opened, True)]


def test_stream_back_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Review Admin #75 : a reconnection on an unchanged position is said."""
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, token="t")
    with caplog.at_level("INFO", logger=bridge_module.log.name):
        bridge.stream_opened()
        assert caplog.messages == []
        bridge.stream_opened(back=True)
    assert caplog.messages == ["stream of g1 back"]


async def test_resignation_not_delivered_is_tried_again() -> None:
    bridge, calls, _board = await _lift_king((None, "timed out"))
    assert calls == [1] and bridge.resign_state is None
    assert bridge.retry_at > time.monotonic()


async def test_king_put_back_in_time_does_not_resign() -> None:
    _bridge, calls, _board = await _lift_king((200, ""), held_for=1.0)
    assert calls == []


async def test_king_missing_during_the_setup_is_not_a_resignation() -> None:
    """Review Junior #75 : before the board ever matched, no resignation."""
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, token="t")
    bridge._post_resign = lambda: pytest.fail("not a resignation")  # type: ignore[method-assign]
    board = Board()
    bridge.apply_stream(snapshot(chess.Board()))
    bridge.on_frame(None, bytearray(frame(_without_king(chess.Board(), chess.WHITE))))
    await bridge.tick(board.write)
    bridge.king_gone_since = time.monotonic() - 10.0
    bridge.settled_since = time.monotonic() - 5.0
    await bridge.tick(board.write)
    assert not bridge.ready and bridge.resign_state is None
    assert board.frames[-1] == {chess.E1}


async def test_opponent_king_off_the_board_is_not_a_resignation() -> None:
    bridge = ready_bridge(after("e4"), chess.BLACK)
    bridge._post_resign = lambda: pytest.fail("not a resignation")  # type: ignore[method-assign]
    bridge.on_frame(None, bytearray(frame(_without_king(after("e4"), chess.WHITE))))
    bridge.king_gone_since = time.monotonic() - 10.0
    await bridge.tick(Board().write)
    assert bridge.king_gone_since is None


async def test_game_over_guides_to_the_final_position_then_blinks_and_stops() -> None:
    mate = after("f3", "e5", "g4", "Qh4#")
    bridge = ready_bridge(after("f3", "e5", "g4"))
    board = Board()
    bridge.apply_stream(snapshot(mate, over=True))
    bridge.on_frame(None, bytearray(frame(placement(after("f3", "e5", "g4")))))
    bridge.settled_since = time.monotonic() - 1.0
    await bridge.tick(board.write)
    assert board.frames[-1] == {chess.D8, chess.H4}
    assert not bridge.finished
    bridge.on_frame(None, bytearray(frame(placement(mate))))
    for _ in range(15):
        await bridge.tick(board.write)
    assert bridge.finished and bridge.stopped.is_set()
    assert set(chess.SQUARES) in board.frames
    assert board.frames[-1] == set()


async def test_after_resigning_the_board_goes_dark_without_the_king() -> None:
    bridge, _calls, board = await _lift_king((200, ""))
    bridge.apply_stream(snapshot(after("e4"), over=True))
    for _ in range(15):
        await bridge.tick(board.write)
    assert bridge.finished and board.frames[-1] == set()


async def test_same_position_with_other_counters_is_not_a_new_one() -> None:
    """Review Junior/Admin #71 : positions are compared, not FEN strings."""
    bridge, posted = await _retry_after_network_error(chess.Board().fen().replace(" 0 1", " 3 7"))
    assert posted == ["e2e4", "e2e4"]


async def test_switch_off_survives_a_dead_link() -> None:
    async def dead(_command: bytes) -> None:
        raise OSError("link lost")

    await bridge_module._switch_off(dead)
    board = Board()
    await bridge_module._switch_off(board.write)
    assert board.frames == [set()]
