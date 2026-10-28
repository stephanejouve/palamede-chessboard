"""Mouse play in the app's window, without a board (#92)."""

from __future__ import annotations

import http.client
import json
import logging

import chess
import pytest
from palamede_chessnut import bridge as bridge_module
from palamede_chessnut import mouse, viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.cli import bridge_from_args
from palamede_chessnut.page import NONCE_PLACEHOLDER, PageServer
from palamede_chessnut.status import RecentLog, snapshot

TOKEN = "secret-token-never-shown"


def stream(board: chess.Board, over: bool = False) -> dict:
    replay = board.root()
    return {
        "fen": board.fen(),
        "ply": board.ply(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "san_history": [replay.san_and_push(m) for m in board.move_stack],
        "is_game_over": over,
    }


def mouse_bridge(board: chess.Board | None = None, player: chess.Color = chess.WHITE) -> Bridge:
    bridge = Bridge("https://viewer.test", "g1", player, TOKEN, with_board=False)
    bridge.apply_stream(stream(board if board is not None else chess.Board()))
    return bridge


class Posts(list):
    """Moves the bridge posts to the viewer ; ``answer[0]`` is what the viewer says."""

    def __init__(self) -> None:
        super().__init__()
        self.answer: list[tuple[int | None, str]] = [(200, "")]

    def __call__(self, url: str, token: str, payload: dict | None = None) -> tuple[int | None, str]:
        self.append((url, token, payload or {}))
        return self.answer[0]


@pytest.fixture
def posts(monkeypatch) -> Posts:
    sent = Posts()
    monkeypatch.setattr(viewer, "post", sent)
    return sent


# ---- what the page may play -----------------------------------------------


def test_dests_on_the_players_turn() -> None:
    dests, promotions = mouse.legal_targets(mouse_bridge())
    assert sorted(dests["e2"]) == ["e3", "e4"]
    assert sorted(dests["g1"]) == ["f3", "h3"]
    assert promotions == []


def test_promotions_are_listed_once() -> None:
    board = chess.Board("8/4P3/8/8/8/8/k7/4K3 w - - 0 1")
    dests, promotions = mouse.legal_targets(mouse_bridge(board))
    assert dests["e7"] == ["e8"]
    assert promotions == ["e7e8"]


@pytest.mark.parametrize("case", ["board", "not my turn", "pending", "over"])
def test_nothing_to_play(case: str) -> None:
    bridge = mouse_bridge(player=chess.BLACK if case == "not my turn" else chess.WHITE)
    if case == "board":
        bridge.with_board = True
    elif case == "pending":
        bridge.pending = chess.Move.from_uci("e2e4")
    elif case == "over":
        bridge.live = False
    assert mouse.legal_targets(bridge) == ({}, [])


def test_snapshot_without_a_board() -> None:
    state = snapshot(mouse_bridge())
    assert state["mouse"] is True
    assert state["lights"]["bluetooth"] == "none"
    assert state["lights"]["sync"] == "none"
    assert "e2" in state["dests"]


def test_snapshot_with_a_board_offers_no_mouse() -> None:
    bridge = Bridge("https://viewer.test", "g1", chess.WHITE, TOKEN)
    bridge.apply_stream(stream(chess.Board()))
    state = snapshot(bridge)
    assert (state["mouse"], state["dests"], state["lights"]["bluetooth"]) == (
        False,
        {},
        "searching",
    )


# ---- the bridge checks, then posts ----------------------------------------


def test_a_legal_move_is_posted_with_the_token(posts) -> None:
    bridge = mouse_bridge()
    assert mouse.play_from_page(bridge, "e2e4") == (200, "ok")
    assert posts == [("https://viewer.test/api/games/g1/move", TOKEN, {"uci": "e2e4"})]
    # Pending until the stream shows it : no second move meanwhile.
    assert bridge.pending == chess.Move.from_uci("e2e4")
    assert mouse.play_from_page(bridge, "d2d4")[0] == 409
    board = chess.Board()
    board.push_san("e4")
    bridge.apply_stream(stream(board))
    assert bridge.pending is None


def test_promotion_carries_the_piece(posts) -> None:
    bridge = mouse_bridge(chess.Board("8/4P3/8/8/8/8/k7/4K3 w - - 0 1"))
    assert mouse.play_from_page(bridge, "e7e8n") == (200, "ok")
    assert posts[0][2] == {"uci": "e7e8n"}


@pytest.mark.parametrize(
    ("uci", "player", "status"),
    [
        ("e2e5", chess.WHITE, 400),  # illegal
        ("e7e5", chess.WHITE, 400),  # the opponent's piece
        ("zz", chess.WHITE, 400),  # unreadable
        ("e7e5", chess.BLACK, 409),  # not the player's turn
    ],
)
def test_refused_before_reaching_the_viewer(posts, uci: str, player: bool, status: int) -> None:
    assert mouse.play_from_page(mouse_bridge(player=player), uci)[0] == status
    assert posts == []


def test_refused_with_a_board(posts) -> None:
    """The board is the reference while connected (21.Ra2 of 008b)."""
    bridge = mouse_bridge()
    bridge.with_board = True
    assert mouse.play_from_page(bridge, "e2e4")[0] == 409
    assert posts == []


def test_refused_once_the_game_is_over(posts) -> None:
    bridge = mouse_bridge()
    bridge.apply_stream(
        {**stream(chess.Board("7k/5QQ1/8/8/8/8/8/K7 b - - 0 1")), "is_game_over": True}
    )
    assert mouse.play_from_page(bridge, "h8g8")[0] == 409
    assert posts == []


@pytest.mark.parametrize(("answer", "status"), [((409, "stale"), 409), ((None, "down"), 502)])
def test_viewer_refusal_or_outage(posts, answer, status: int) -> None:
    posts.answer[0] = answer
    bridge = mouse_bridge()
    assert mouse.play_from_page(bridge, "e2e4")[0] == status
    assert bridge.pending is None  # the player may try again


def test_one_move_at_a_time(posts) -> None:
    with mouse._posting:
        assert mouse.play_from_page(mouse_bridge(), "e2e4")[0] == 409
    assert posts == []


def test_the_token_is_never_logged(posts, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    bridge = mouse_bridge()
    mouse.play_from_page(bridge, "e2e4")
    posts.answer[0] = (403, "not your turn")
    mouse.play_from_page(mouse_bridge(), "d2d4")
    assert "e2e4" in caplog.text
    assert TOKEN not in caplog.text


# ---- page server : POST /move ---------------------------------------------


@pytest.fixture
def page(posts):
    server = PageServer(mouse_bridge(), RecentLog())
    server.start()
    yield server
    server.shutdown()
    server.server_close()


def request(
    server: PageServer,
    method: str,
    path: str,
    body: bytes | None = None,
    **headers: str,
) -> tuple[int, bytes]:
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


MOVE = json.dumps({"uci": "e2e4"}).encode()


def test_the_page_carries_the_nonce_of_this_launch(page) -> None:
    status, html = request(page, "GET", "/")
    assert status == 200
    assert page.nonce.encode() in html
    assert NONCE_PLACEHOLDER not in html
    assert len(page.nonce) >= 40  # 32 random bytes, base64
    assert PageServer(mouse_bridge()).nonce != page.nonce


def test_a_move_from_the_page(page, posts) -> None:
    status, body = request(page, "POST", "/move", MOVE, **good_headers(page))
    assert (status, json.loads(body)) == (200, {"ok": True})
    assert posts[0][2] == {"uci": "e2e4"}
    assert TOKEN.encode() not in body


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"X-Palamede-Nonce": None}, 403),
        ({"X-Palamede-Nonce": "guess"}, 403),
        ({"X-Palamede-Nonce": "é"}, 403),
        ({"Origin": None}, 403),
        ({"Origin": "https://evil.test"}, 403),
        ({"Origin": "null"}, 403),
        ({"Host": "evil.test:1"}, 421),
        ({"Content-Type": "application/x-www-form-urlencoded"}, 415),
        ({"Content-Type": None}, 415),
    ],
)
def test_page_post_refusals(page, posts, change: dict, status: int) -> None:
    headers = good_headers(page)
    for name, value in change.items():
        if value is None:
            headers.pop(name)
        else:
            headers[name] = value
    if "Host" in change:
        headers["Origin"] = "http://evil.test:1"
    assert request(page, "POST", "/move", MOVE, **headers)[0] == status
    assert posts == []


@pytest.mark.parametrize(
    ("body", "status"),
    [(b"{}", 400), (b'{"uci": 5}', 400), (b"not json", 400), (b"x" * 300, 413)],
)
def test_page_post_bodies(page, posts, body: bytes, status: int) -> None:
    assert request(page, "POST", "/move", body, **good_headers(page))[0] == status
    assert posts == []


def test_page_post_only_on_move(page, posts) -> None:
    assert request(page, "POST", "/state", MOVE, **good_headers(page))[0] == 404
    assert posts == []


def test_page_post_of_an_illegal_move_says_why(page, posts) -> None:
    body = json.dumps({"uci": "e2e5"}).encode()
    status, answer = request(page, "POST", "/move", body, **good_headers(page))
    assert (status, json.loads(answer)) == (400, {"error": "coup illégal"})


# ---- without a board, the bridge follows the stream until the end ---------


def test_watch_game_stops_at_the_end() -> None:
    bridge = mouse_bridge()
    bridge.apply_stream(
        {**stream(chess.Board("7k/5QQ1/8/8/8/8/8/K7 b - - 0 1")), "is_game_over": True}
    )
    # Fails instead of hanging the suite if it never stops (review Junior #97).
    asyncio = bridge_module.asyncio
    asyncio.run(asyncio.wait_for(bridge.watch_game(), timeout=2))
    assert bridge.finished and bridge.stopped.is_set()


def test_run_without_a_board_never_looks_for_one(monkeypatch) -> None:
    bridge = mouse_bridge()
    monkeypatch.setattr(bridge, "_follow_stream", lambda _loop: None)

    async def no_scan() -> None:
        raise AssertionError("looked for a board")

    async def watched() -> None:
        bridge.stopped.set()

    monkeypatch.setattr(bridge, "run_board", no_scan)
    monkeypatch.setattr(bridge, "watch_game", watched)
    bridge_module.asyncio.run(bridge.run())
    assert bridge.stopped.is_set()


def test_no_board_is_an_app_option(monkeypatch) -> None:
    monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", TOKEN)
    argv = ["--viewer", "https://viewer.test", "--game", "g1", "--color", "white"]
    assert bridge_from_args(argv, "t", app=True).with_board is True
    assert bridge_from_args([*argv, "--no-board"], "t", app=True).with_board is False
    with pytest.raises(SystemExit):
        bridge_from_args([*argv, "--no-board"], "t")  # the CLI has no window


# ---- a lost answer (review Admin #97) -------------------------------------


def lost_then(bridge: Bridge, posts: Posts, server: chess.Board | None) -> None:
    """``e2e4`` reaches the viewer but its answer is lost ; then the viewer says ``server``."""
    posts.answer[0] = (None, "timed out")
    assert mouse.play_from_page(bridge, "e2e4")[0] == 502
    posts.answer[0] = (200, "")
    snapshot = None if server is None else stream(server)
    bridge.server_position = lambda: snapshot  # type: ignore[method-assign]


def test_a_move_that_had_landed_is_not_followed_blind(posts) -> None:
    """The opponent already answered : the next move waits for the new position."""
    bridge = mouse_bridge()
    after = chess.Board()
    for san in ("e4", "e5"):
        after.push_san(san)
    lost_then(bridge, posts, after)
    status, message = mouse.play_from_page(bridge, "d2d4")
    assert status == 409 and "bien arrivé" in message
    assert [p[2]["uci"] for p in posts] == ["e2e4"]  # d2d4 never posted
    assert bridge.game is not None and bridge.game.board_fen() == after.board_fen()
    assert bridge.undelivered is None
    assert bridge.history == ["e4", "e5"]  # the move list follows (review Admin #97)
    assert mouse.play_from_page(bridge, "d2d4") == (200, "ok")  # now seen, now played


def test_a_move_that_had_not_landed_is_replaced(posts) -> None:
    bridge = mouse_bridge()
    lost_then(bridge, posts, chess.Board())
    assert mouse.play_from_page(bridge, "d2d4") == (200, "ok")
    assert [p[2]["uci"] for p in posts] == ["e2e4", "d2d4"]
    assert bridge.undelivered is None


def test_an_unreadable_viewer_posts_nothing(posts) -> None:
    bridge = mouse_bridge()
    lost_then(bridge, posts, None)
    assert mouse.play_from_page(bridge, "d2d4")[0] == 502
    assert [p[2]["uci"] for p in posts] == ["e2e4"]
    assert bridge.undelivered == chess.Move.from_uci("e2e4")  # still checked next time


def test_page_accepts_the_localhost_origin(page, posts) -> None:
    headers = {**good_headers(page), "Origin": f"http://localhost:{page.port}"}
    headers["Host"] = f"localhost:{page.port}"
    assert request(page, "POST", "/move", MOVE, **headers)[0] == 200


def test_the_token_is_in_no_answer_of_the_page(page, posts) -> None:
    """Every answer of POST /move, whatever its status (review Junior #97)."""
    answers = []
    for uci in ("e2e5", "e7e5", "e2e4", "d2d4"):  # 400, 400, 200, 409 (pending)
        body = json.dumps({"uci": uci}).encode()
        answers.append(request(page, "POST", "/move", body, **good_headers(page)))
    page.bridge.pending = None
    posts.answer[0] = (409, "stale")  # the viewer refuses : its own branch (review Junior #97)
    answers.append(request(page, "POST", "/move", MOVE, **good_headers(page)))
    assert json.loads(answers[-1][1]) == {"error": "coup refusé par le serveur"}
    posts.answer[0] = (None, "down")
    answers.append(request(page, "POST", "/move", MOVE, **good_headers(page)))  # 502
    answers.append(request(page, "POST", "/move", MOVE, **{"X-Palamede-Nonce": "x"}))  # 403
    assert {status for status, _ in answers} == {200, 400, 403, 409, 502}
    for _status, body in answers:
        assert TOKEN.encode() not in body
        assert b"Bearer" not in body


def test_the_page_offers_to_flip_the_board(page) -> None:
    """#91 : display only, nothing new on the bridge's side."""
    status, html = request(page, "GET", "/")
    assert status == 200 and b'id="flip-btn"' in html


def test_server_position_keeps_the_moves(monkeypatch) -> None:
    board = chess.Board()
    positions = [{"fen": board.fen(), "san": None}]
    for san in ("e4", "c5"):
        board.push_san(san)
        positions.append({"fen": board.fen(), "san": san})
    monkeypatch.setattr(viewer, "get_json", lambda _url: {"positions": positions, "result": "*"})
    snap = mouse_bridge().server_position()
    assert snap is not None
    assert (snap["fen"], snap["san_history"], snap["is_game_over"]) == (
        board.fen(),
        ["e4", "c5"],
        False,
    )
