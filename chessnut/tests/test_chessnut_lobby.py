"""No game on the board : two rooks lifted for 5 s open one (#167)."""

from __future__ import annotations

import io
import json
import threading
import time
import urllib.error

import chess
import pytest
from palamede_chessnut import config, leds, lobby, viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.cli import bridge_from_args
from palamede_chessnut.games import NO_GAME, Session, successor
from palamede_chessnut.lobby import (
    CHOICE_SECONDS,
    CORNERS,
    GESTURE_SECONDS,
    NO_SERVER,
    TOGETHER_SECONDS,
    Lobby,
    choices,
    chosen,
    gesture_side,
    on_the_way,
)
from palamede_chessnut.page import PageServer
from palamede_chessnut.status import snapshot, sync_light

TOKEN = "secret-token-never-shown"
VIEWER = "https://viewer.test"
NEW = "tablier.stephane.20261101-190000-abcd"


def lifted(*squares: chess.Square) -> chess.BaseBoard:
    board = chess.BaseBoard()
    for square in squares:
        board.remove_piece_at(square)
    return board


def held(bridge: Bridge, placement: chess.BaseBoard, seconds: float) -> None:
    """``placement`` on the board, still for ``seconds``."""
    bridge.observed = placement
    bridge.settled_since = time.monotonic() - seconds


class Board:
    """What the lobby writes to the board : the LED frames, in order."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    async def __call__(self, command: bytes) -> None:
        self.frames.append(command)


class Server:
    """``POST /api/games`` : records the payloads, answers ``(status, body)``."""

    def __init__(self, monkeypatch, status: int | None = 200, body: object = None) -> None:
        self.answer = (status, {"status": "ok", "game_id": NEW} if body is None else body)
        self.posts: list[tuple[str, str, dict]] = []
        monkeypatch.setattr(viewer, "post_json", self.post_json)

    def post_json(self, url: str, token: str, payload: dict):
        self.posts.append((url, token, payload))
        return self.answer


def new_lobby(*, armed: bool = True, **kwargs) -> Lobby:
    """A lobby ; ``armed`` : the whole starting position has stood on its board,
    its four corners seen occupied."""
    board = Lobby(VIEWER, TOKEN, **kwargs)
    board.armed = armed
    if armed:
        board.emptied = dict.fromkeys(CORNERS)
    return board


def at(bridge: Bridge, placement: chess.BaseBoard, since: float) -> None:
    """``placement`` on the board since ``since`` (``time.monotonic()`` scale)."""
    bridge.observed = placement
    bridge.settled_since = since


# ---- the gesture ----------------------------------------------------------


def emptied_at(**times: float) -> dict[chess.Square, float | None]:
    """The four corners seen occupied, those named emptied at their time."""
    corners: dict[chess.Square, float | None] = dict.fromkeys(CORNERS)
    corners.update({chess.parse_square(name): time for name, time in times.items()})
    return corners


@pytest.mark.parametrize(
    ("placement", "emptied", "side"),
    [
        (lifted(chess.A1, chess.H1), emptied_at(a1=10, h1=10), chess.WHITE),
        (lifted(chess.A8, chess.H8), emptied_at(a8=10, h8=10.5), chess.BLACK),
        (chess.BaseBoard(), emptied_at(), None),
        (lifted(chess.A1), emptied_at(a1=10), None),
        (lifted(chess.A1, chess.H8), emptied_at(a1=10, h8=10), None),
        # four corners : out of scope, and the other side's must stay occupied
        (lifted(*CORNERS), emptied_at(a1=10, h1=10, a8=10, h8=10), None),
        (lifted(chess.E1), emptied_at(), None),  # the king alone is resigning, not this
        # the rest of the board may be anything : the queen one square off (smoke 02/11)
        (lifted(chess.A1, chess.H1, chess.E2, chess.D8), emptied_at(a1=10, h1=10), chess.WHITE),
        # one corner, then the other later : putting the pieces away (owner 05/11)
        (lifted(chess.A1, chess.H1), emptied_at(a1=10, h1=10 + TOGETHER_SECONDS + 0.1), None),
        # never seen occupied : set up with the corners last (review #170)
        (lifted(chess.A1, chess.H1), {chess.A8: None, chess.H8: None}, None),
    ],
)
def test_two_corners_of_one_side_emptied_together(placement, emptied, side) -> None:
    assert gesture_side(placement, emptied) == side


def test_the_corner_piece_is_not_looked_at() -> None:
    # A Chess960 start keeps a piece in each corner, not always a rook.
    board = chess.BaseBoard()
    board.set_piece_at(chess.A8, chess.Piece(chess.KNIGHT, chess.BLACK))
    board.remove_piece_at(chess.A1)
    board.remove_piece_at(chess.H1)
    assert gesture_side(board, emptied_at(a1=3, h1=3)) == chess.WHITE


async def test_the_gesture_held_opens_the_game_with_the_defaults(monkeypatch) -> None:
    server = Server(monkeypatch)
    board = new_lobby()
    held(board, lifted(chess.A8, chess.H8), GESTURE_SECONDS + 0.1)
    await board.tick(Board())
    assert server.posts == [
        (
            f"{VIEWER}/api/games",
            TOKEN,
            {"opponent": "bot-maia-1100", "color": "black", "time_control": {"format": "15+10"}},
        )
    ]
    assert board.created == NEW and board.player == chess.BLACK
    assert board.animation == leds.chaser(7)


async def test_the_settings_choose_the_opponent_and_the_clock(monkeypatch) -> None:
    server = Server(monkeypatch)
    board = new_lobby(new_game={"opponent": "bot-maia-1500", "time_control": "15+10"})
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS)
    await board.tick(Board())
    assert server.posts[0][2] == {
        "opponent": "bot-maia-1500",
        "color": "white",
        "time_control": {"format": "15+10"},
    }


async def test_before_5_s_the_rooks_blink_and_nothing_is_asked(monkeypatch) -> None:
    server = Server(monkeypatch)
    board = new_lobby()
    held(board, lifted(chess.A1, chess.H1), 2.1)
    await board.tick(Board())
    assert server.posts == []
    assert board.leds in (frozenset({chess.A1, chess.H1}), frozenset())
    assert board.lobby_text() == "Nouvelle partie avec les Blancs dans 3 s…"


async def test_the_rooks_back_before_5_s_cancel(monkeypatch) -> None:
    server = Server(monkeypatch)
    board, write = new_lobby(), Board()
    held(board, lifted(chess.A1, chess.H1), 4)
    await board.tick(write)
    held(board, chess.BaseBoard(), 10)
    await board.tick(write)
    assert server.posts == [] and board.leds == frozenset()
    assert board.lobby_text() == "Aucune partie, cadence 15+10."


async def test_pieces_set_up_with_the_rooks_last_open_nothing(monkeypatch) -> None:
    server = Server(monkeypatch)
    board, write = new_lobby(armed=False), Board()
    held(board, lifted(chess.A1, chess.H1, chess.D1), 1)  # setting up
    await board.tick(write)
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS + 1)  # only the rooks left
    await board.tick(write)
    assert server.posts == [] and board.counting is None
    assert board.leds == frozenset()


async def test_one_corner_then_the_other_within_a_second(monkeypatch) -> None:
    server = Server(monkeypatch)
    board, write = new_lobby(armed=False), Board()
    now = time.monotonic()
    for placement, since in [
        (chess.BaseBoard(), now - 10),
        (lifted(chess.H8), now - 6),
        (lifted(chess.A8, chess.H8), now - 6 + TOGETHER_SECONDS / 2),
    ]:
        at(board, placement, since)
        await board.tick(write)
    assert [payload["color"] for _url, _token, payload in server.posts] == ["black"]


async def test_the_pieces_put_away_one_by_one_open_nothing(monkeypatch) -> None:
    server = Server(monkeypatch)
    board, write = new_lobby(armed=False), Board()
    now = time.monotonic()
    for placement, since in [
        (lifted(chess.E4, chess.D5), now - 30),  # a finished game's last position
        (lifted(chess.E4, chess.D5, chess.A1), now - 20),
        (lifted(chess.E4, chess.D5, chess.A1, chess.H1), now - 17),
    ]:
        at(board, placement, since)
        await board.tick(write)
    assert server.posts == [] and board.counting is None


async def test_from_any_placement_the_game_opens_then_its_bridge_guides(monkeypatch) -> None:
    # The black queen on e8 (smoke of Stéphane, 02/11) : the gesture no longer waits for it.
    server = Server(monkeypatch)
    board, write = new_lobby(armed=False), Board()
    queen_off = chess.BaseBoard()
    queen_off.remove_piece_at(chess.D8)
    queen_off.remove_piece_at(chess.E8)
    queen_off.set_piece_at(chess.E8, chess.Piece(chess.QUEEN, chess.BLACK))
    now = time.monotonic()
    at(board, queen_off, now - 10)
    await board.tick(write)
    without = queen_off.copy()
    without.remove_piece_at(chess.A1)
    without.remove_piece_at(chess.H1)
    at(board, without, now - GESTURE_SECONDS - 0.1)
    await board.tick(write)
    assert [payload["color"] for _url, _token, payload in server.posts] == ["white"]
    assert not board.armed  # the knights' clock still wants the whole start


async def test_after_a_try_the_corners_must_be_seen_occupied_again(monkeypatch) -> None:
    server = Server(monkeypatch, 429, {})
    board, write = new_lobby(), Board()
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS)
    await board.tick(write)
    held(board, lifted(chess.A1, chess.H1, chess.E2), GESTURE_SECONDS)  # another piece moved
    await board.tick(write)
    assert len(server.posts) == 1


async def test_the_chaser_then_the_window_moves_to_the_new_game(monkeypatch) -> None:
    Server(monkeypatch)
    board, write = new_lobby(), Board()
    told = threading.Event()
    opened: list[str] = []
    board.on_created = lambda game_id: (opened.append(game_id), told.set(), (200, ""))[2]
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS)
    await board.tick(write)
    while board.animation:
        await board.tick(write)
    assert told.wait(2) and opened == [NEW]
    assert board.lobby_text() == f"Partie {NEW} ouverte : la fenêtre la rejoint."


async def test_a_game_opened_but_not_shown_is_said(monkeypatch) -> None:
    Server(monkeypatch)
    board = new_lobby()
    board.on_created = lambda _game_id: (502, "Le serveur ne répond pas.")
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS)
    await board.tick(Board())
    board.animation = []
    board.created = NEW
    board._hand_over()
    assert board.created is None
    assert board.lobby_text() == f"Partie {NEW} créée, mais pas ouverte : Le serveur ne répond pas."


# ---- refusals --------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "said"),
    [
        (
            429,
            {"detail": "Déjà 5 parties Tablier en cours : terminez-en une d'abord"},
            "Partie non créée : Déjà 5 parties Tablier en cours : terminez-en une d'abord",
        ),
        (
            422,
            {"detail": "Adversaire inconnu : bot-x"},
            "Partie non créée : Adversaire inconnu : bot-x",
        ),
        (429, None, lobby.REFUSALS[429]),
        (422, "not a dict", lobby.REFUSALS[422]),
        (500, None, "Partie non créée (HTTP 500)."),
        (None, None, NO_SERVER),
        (200, {"status": "ok"}, lobby.UNREADABLE),  # no game_id : maybe opened, nothing to open
    ],
)
async def test_a_refusal_is_said_without_a_light(monkeypatch, status, body, said) -> None:
    Server(monkeypatch, status, body if body is not None else {})
    board, write = new_lobby(), Board()
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS)
    await board.tick(write)
    assert board.lobby_text() == said
    assert board.created is None and board.animation == []
    assert board.leds == frozenset()


async def test_a_refused_token_turns_the_light_red(monkeypatch) -> None:
    Server(monkeypatch, 401, {"detail": "Jeton de joueur requis ou invalide (UNAUTHENTICATED)"})
    board = new_lobby()
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS)
    await board.tick(Board())
    assert board.token_light == "refused"


async def test_a_refusal_is_not_tried_again_until_the_rooks_move(monkeypatch) -> None:
    server = Server(monkeypatch, 429, {})
    board, write = new_lobby(), Board()
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS)
    await board.tick(write)
    await board.tick(write)
    assert len(server.posts) == 1
    assert board.lobby_text() == lobby.REFUSALS[429]
    held(board, chess.BaseBoard(), 1)  # rooks back
    await board.tick(write)
    assert board.lobby_text().startswith("Aucune partie")
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS)  # lifted again
    await board.tick(write)
    assert len(server.posts) == 2


# ---- the clock chosen by a knight (#186) ---------------------------------------


def knight(home: chess.Square, square: chess.Square | None) -> chess.BaseBoard:
    """The starting position, the knight of ``home`` lifted or put on ``square``."""
    board = lifted(home)
    if square is not None:
        board.set_piece_at(square, chess.Piece(chess.KNIGHT, chess.WHITE))
    return board


@pytest.mark.parametrize(
    ("placement", "picked"),
    [
        (knight(chess.G1, chess.A3), ((0, 3, chess.A3),)),
        (knight(chess.G1, chess.F3), ((0, 90, chess.F3),)),
        (knight(chess.G1, chess.E3), ((0, 25, chess.E3),)),
        (knight(chess.B1, chess.A4), ((1, 0, chess.A4),)),
        (knight(chess.B1, chess.F4), ((1, 30, chess.F4),)),
        (knight(chess.B1, chess.D4), ((1, 5, chess.D4),)),
        # Either knight, the rank says the part (smoke of Stéphane, 04/11).
        (knight(chess.G1, chess.A4), ((1, 0, chess.A4),)),
        (knight(chess.B1, chess.C3), ((0, 10, chess.C3),)),
        (knight(chess.G1, chess.G3), ()),  # beyond the six values
        (knight(chess.G1, chess.H3), ()),
        (knight(chess.G1, chess.D5), ()),  # rank 5 says nothing
        (knight(chess.G1, None), ()),  # lifted : on its way
        (chess.BaseBoard(), ()),
    ],
)
def test_the_knight_s_square_says_the_part_and_the_value(placement, picked) -> None:
    assert choices(placement) == picked
    assert gesture_side(placement, emptied_at()) is None


def test_both_knights_off_say_both_parts() -> None:
    """The b1 knight left on b4 while the g1 one goes to f3 (smoke of Stéphane, 04/11)."""
    placement = knight(chess.B1, chess.B4)
    placement.remove_piece_at(chess.G1)
    placement.set_piece_at(chess.F3, chess.Piece(chess.KNIGHT, chess.WHITE))
    assert set(choices(placement)) == {(1, 2, chess.B4), (0, 90, chess.F3)}
    assert on_the_way(placement)


@pytest.mark.parametrize(
    "placement",
    [
        knight(chess.G1, chess.D3),
        knight(chess.G1, None),
        knight(chess.G1, chess.H5),  # off its rank : still on its way, chooses nothing
        knight(chess.B1, chess.C3),
        lifted(chess.G1, chess.B1),  # both lifted
    ],
)
def test_a_choosing_knight_off_keeps_the_lobby_armed(placement) -> None:
    assert on_the_way(placement)


@pytest.mark.parametrize(
    "placement",
    [
        lifted(chess.C1, chess.F1),  # the two bishops : the problem, later
        lifted(chess.G1, chess.F1),  # the notches of #189 are gone
        lifted(chess.G8),  # a black knight
    ],
)
def test_other_pieces_off_choose_nothing(placement) -> None:
    assert choices(placement) == () and gesture_side(placement, emptied_at()) is None
    assert not on_the_way(placement)


def spare(
    piece: chess.Piece, square: chess.Square, home: chess.Square = chess.G1
) -> chess.BaseBoard:
    """``home`` emptied, ``piece`` put on ``square`` : not the knight that left."""
    board = lifted(home)
    board.set_piece_at(square, piece)
    return board


@pytest.mark.parametrize(
    "placement",
    [
        # The board comes with spare queens (review Junior #191).
        spare(chess.Piece(chess.QUEEN, chess.WHITE), chess.D3),
        spare(chess.Piece(chess.KNIGHT, chess.BLACK), chess.D3),
        # The knight on a pawn's square : not a square of its own.
        spare(chess.Piece(chess.KNIGHT, chess.WHITE), chess.E2),
        # A third white knight : both homes still full, or one left for two knights out.
        chess.BaseBoard("rnbqkbnr/pppppppp/8/8/8/3N4/PPPPPPPP/RNBQKBNR"),
        chess.BaseBoard("rnbqkbnr/pppppppp/8/8/3N4/N7/PPPPPPPP/R1BQKBNR"),
    ],
)
def test_only_the_knight_itself_on_an_empty_square_chooses(placement) -> None:
    assert choices(placement) == () and not on_the_way(placement)


@pytest.mark.parametrize(
    ("clock", "part", "value", "after"),
    [
        ("15+10", 0, 90, "90+10"),
        ("15+10", 1, 0, "15+0"),
        ("7+1", 0, 3, "3+1"),
        ("unrated", 0, 25, "25+10"),  # no clock : from the default one
        ("unrated", 1, 2, "15+2"),
    ],
)
def test_the_value_replaces_its_part(clock, part, value, after) -> None:
    assert chosen(clock, ((part, value, chess.A3),)) == after


async def test_the_knight_still_2_s_chooses_the_base_without_a_game(monkeypatch) -> None:
    server = Server(monkeypatch)
    board = new_lobby()
    held(board, knight(chess.G1, chess.F3), CHOICE_SECONDS)
    await board.tick(Board())
    assert server.posts == []
    assert board.new_game["time_control"] == "90+10"
    assert board.animation == leds.chosen(frozenset({chess.F3}))
    assert leds.chosen(frozenset({chess.F3}))[0] == frozenset({chess.F3})
    assert board.lobby_text() == "Aucune partie, cadence 90+10."


async def test_the_b1_knight_on_a4_chooses_no_increment(monkeypatch) -> None:
    Server(monkeypatch)
    board = new_lobby()
    held(board, knight(chess.B1, chess.A4), CHOICE_SECONDS + 0.5)
    await board.tick(Board())
    assert board.new_game["time_control"] == "15+0"


async def test_before_2_s_the_square_blinks_and_the_window_says_the_clock(monkeypatch) -> None:
    Server(monkeypatch)
    board = new_lobby()
    held(board, knight(chess.G1, chess.B3), 0.6)
    await board.tick(Board())
    assert board.new_game["time_control"] == "15+10"
    assert board.leds in (frozenset({chess.B3}), frozenset())
    assert board.lobby_text() == "Cadence 5+10 dans 1 s…"


async def test_a_square_crossed_on_the_way_chooses_nothing(monkeypatch) -> None:
    Server(monkeypatch)
    board, write = new_lobby(), Board()
    held(board, knight(chess.G1, chess.C3), 0.5)  # passing by c3
    await board.tick(write)
    held(board, knight(chess.G1, chess.E3), CHOICE_SECONDS)
    await board.tick(write)
    assert board.new_game["time_control"] == "25+10"


async def test_one_choice_per_placement_another_square_another_choice(monkeypatch) -> None:
    Server(monkeypatch)
    board, write = new_lobby(), Board()
    held(board, knight(chess.G1, chess.A3), CHOICE_SECONDS)
    await board.tick(write)
    board.animation = []
    await board.tick(write)  # still there : chosen once
    assert board.new_game["time_control"] == "3+10"
    held(board, knight(chess.G1, chess.D3), CHOICE_SECONDS)
    await board.tick(write)
    assert board.new_game["time_control"] == "15+10"


def two_knights(b1_on: chess.Square, g1_on: chess.Square | None) -> chess.BaseBoard:
    """The b1 knight on ``b1_on``, the g1 one lifted or on ``g1_on``."""
    board = knight(chess.B1, b1_on)
    board.remove_piece_at(chess.G1)
    if g1_on is not None:
        board.set_piece_at(g1_on, chess.Piece(chess.KNIGHT, chess.WHITE))
    return board


async def test_a_knight_left_out_does_not_stop_the_other(monkeypatch, caplog) -> None:
    """Smoke of Stéphane, 04/11 : b1 left on b4, the g1 knight on f3 did nothing."""
    Server(monkeypatch)
    board, write = new_lobby(), Board()
    held(board, knight(chess.B1, chess.B4), CHOICE_SECONDS)
    await board.tick(write)
    board.animation = []
    held(board, two_knights(chess.B4, None), 1)
    await board.tick(write)
    assert board.armed
    with caplog.at_level("INFO", logger="palamede_chessnut.lobby"):
        held(board, two_knights(chess.B4, chess.F3), CHOICE_SECONDS)
        await board.tick(write)
    assert board.new_game["time_control"] == "90+2"
    assert board.animation == leds.chosen(frozenset({chess.F3}))  # b4, taken, does not blink
    assert "clock chosen at f3 : 15+2 → 90+2" in caplog.text


async def test_the_knight_put_last_wins_its_rank_and_a_crossing_retakes_nothing(
    monkeypatch,
) -> None:
    """Review Admin #191 : b1 on c3 then g1 on f3, both on rank 3."""
    Server(monkeypatch)
    board, write = new_lobby(), Board()
    held(board, knight(chess.B1, chess.C3), CHOICE_SECONDS)
    await board.tick(write)
    board.animation = []
    assert board.new_game["time_control"] == "10+10"
    held(board, two_knights(chess.C3, chess.F3), CHOICE_SECONDS)
    await board.tick(write)
    board.animation = []
    assert board.new_game["time_control"] == "90+10"
    # The g1 knight goes home : c3, still standing, is not taken again.
    held(board, knight(chess.B1, chess.C3), CHOICE_SECONDS)
    await board.tick(write)
    assert board.new_game["time_control"] == "90+10" and board.animation == []


async def test_a_knight_back_on_its_square_chooses_again(monkeypatch) -> None:
    """Gone, the square is forgotten : the same knight put back there is taken again."""
    Server(monkeypatch)
    board, write = new_lobby(), Board()
    held(board, knight(chess.B1, chess.C3), CHOICE_SECONDS)
    await board.tick(write)
    board.animation = []
    held(board, knight(chess.B1, None), 1)
    await board.tick(write)
    held(board, knight(chess.B1, chess.C3), CHOICE_SECONDS)
    await board.tick(write)
    assert board.animation == leds.chosen(frozenset({chess.C3}))


async def test_the_log_tells_a_knight_without_value_and_a_disarmed_lobby(
    monkeypatch, caplog
) -> None:
    """Smoke of Stéphane, 04/11 : the log must tell a gesture not seen from one not made."""
    Server(monkeypatch)
    board, write = new_lobby(), Board()
    with caplog.at_level("INFO", logger="palamede_chessnut.lobby"):
        held(board, knight(chess.G1, chess.H3), CHOICE_SECONDS)
        await board.tick(write)
        await board.tick(write)  # once per placement
        held(board, lifted(chess.E2), 1)
        await board.tick(write)
    assert caplog.text.count("knight on h3 : no value there") == 1
    assert "gestures disarmed until the starting position : " in caplog.text
    assert not board.armed


async def test_two_knights_put_together_on_one_rank_choose_nothing_and_say_so(
    monkeypatch,
) -> None:
    """Review Junior #191 : b1 on c3 and g1 on f3 within the same 2 s."""
    Server(monkeypatch)
    board, write = new_lobby(), Board()
    held(board, two_knights(chess.C3, chess.F3), CHOICE_SECONDS)
    await board.tick(write)
    assert board.new_game["time_control"] == "15+10" and board.animation == []
    assert board.lobby_text() == "Deux cavaliers sur la rangée 3 : retirez-en un."
    held(board, knight(chess.B1, chess.C3), CHOICE_SECONDS)
    await board.tick(write)
    assert board.new_game["time_control"] == "10+10" and board.clash == []


async def test_arming_and_disarming_are_logged_once_each(monkeypatch, caplog) -> None:
    """Review Junior #191 : logged on the change only, not at every tick."""
    Server(monkeypatch)
    board, write = new_lobby(armed=False), Board()
    with caplog.at_level("INFO", logger="palamede_chessnut.lobby"):
        held(board, chess.BaseBoard(), 1)
        await board.tick(write)
        await board.tick(write)
        held(board, lifted(chess.E2), 1)
        await board.tick(write)
        await board.tick(write)
    assert caplog.text.count("gestures armed") == 1
    assert caplog.text.count("gestures disarmed") == 1


async def test_the_value_already_in_place_is_said(monkeypatch) -> None:
    """Stéphane, 04/11 : « 15+2 → 15+2 » showed nothing, as if the gesture failed."""
    Server(monkeypatch)
    board, write = new_lobby(), Board()
    held(board, knight(chess.B1, chess.F4), 1)
    await board.tick(write)
    assert board.lobby_text() == "Cadence 15+30 dans 1 s…"
    held(board, knight(chess.B1, chess.E4), 1)
    board.new_game["time_control"] = "15+10"
    await board.tick(write)
    assert board.lobby_text() == "Cadence 15+10 (déjà en place) dans 1 s…"


async def test_the_chosen_clock_is_the_next_game_s(monkeypatch) -> None:
    server = Server(monkeypatch)
    board, write = new_lobby(), Board()
    for placement in (knight(chess.G1, chess.A3), knight(chess.B1, chess.B4)):
        held(board, placement, CHOICE_SECONDS)
        await board.tick(write)
        board.animation = []
        held(board, chess.BaseBoard(), 1)
        await board.tick(write)
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS)
    await board.tick(write)
    assert server.posts[0][2]["time_control"] == {"format": "3+2"}


async def test_pieces_set_up_with_a_knight_on_rank_3_choose_nothing(monkeypatch) -> None:
    Server(monkeypatch)
    board = new_lobby(armed=False)
    held(board, knight(chess.G1, chess.C3), CHOICE_SECONDS + 1)
    await board.tick(Board())
    assert board.new_game["time_control"] == "15+10" and board.choosing == ()


def test_without_a_clock_the_window_says_so() -> None:
    board = new_lobby(new_game={"opponent": "bot-maia-1100", "time_control": "unrated"})
    held(board, chess.BaseBoard(), 1)
    assert board.lobby_text() == "Aucune partie, cadence sans pendule."


def test_the_chosen_clock_outlives_the_game_it_opened() -> None:
    """Lobby → its game → the next lobby : the clock chosen stays (mutant of Junior, #189)."""
    first = new_lobby()
    first.new_game["time_control"] = "3+2"
    game = successor(first, NEW, with_board=True)
    assert game.new_game == {"opponent": "bot-maia-1100", "time_control": "3+2"}
    game.finished = True
    session = Session(game, lambda _bridge: None)
    session._idle(game)
    try:
        assert isinstance(session.bridge, Lobby)
        assert session.bridge.new_game["time_control"] == "3+2"
        assert config.NEW_GAME["time_control"] == "15+10"  # the default untouched
    finally:
        session.close()


# ---- the page --------------------------------------------------------------


def finished_bridge() -> Bridge:
    bridge = Bridge(VIEWER, "009", chess.BLACK, TOKEN)
    bridge.game = chess.Board("4k3/8/8/8/8/8/8/4K2q w - - 0 1")
    bridge.history, bridge.finished = ["Qh1#"], True
    bridge.account, bridge.token_light, bridge.stream_link = "stephane", "on", "connected"
    return bridge


def test_the_page_says_how_to_open_a_game() -> None:
    board = new_lobby()
    held(board, chess.BaseBoard(), 1)
    state = snapshot(board)
    assert state["lobby"] == "Aucune partie, cadence 15+10."
    assert state["tips"]["f3"] == "90′" and state["tips"]["a4"] == "+0"
    assert state["game_id"] == "" and state["fen"] is None
    assert state["setup"] is None and state["differing"] == []
    assert state["lights"]["sync"] == "waiting"


def test_a_finished_game_stays_on_screen_its_board_no_longer_compared() -> None:
    board = new_lobby(after=finished_bridge())
    held(board, chess.BaseBoard(), 1)
    state = snapshot(board)
    assert state["game_id"] == "009" and state["history"] == ["Qh1#"]
    assert state["finished"] and state["account"] == "stephane"
    assert state["lobby"] == "Partie terminée, cadence 15+10."
    assert state["setup"] is None and state["differing"] == []
    assert sync_light(board) == "waiting"


def test_after_a_game_over_the_lobby_draws_the_board_as_read() -> None:
    """#261 : the pieces set up for the next game, not the finished game's last position."""
    done = finished_bridge()
    board = new_lobby(after=done)
    knight_out = chess.Board()
    knight_out.push_san("Nc3")
    held(board, chess.BaseBoard(knight_out.board_fen()), 1)
    state = snapshot(board)
    assert state["fen"] == done.game.fen()  # the FEN field and the moves stay the game's
    assert state["screen"] == knight_out.board_fen() != chess.Board(state["fen"]).board_fen()


def test_a_lobby_without_a_board_read_draws_nothing_new() -> None:
    assert snapshot(new_lobby())["screen"] is None
    done = finished_bridge()
    assert snapshot(new_lobby(after=done))["screen"] == done.game.fen()


def test_a_game_s_bridge_draws_its_game_not_the_board() -> None:
    bridge = finished_bridge()
    bridge.observed = chess.BaseBoard()
    assert snapshot(bridge)["screen"] == bridge.game.fen()


def test_the_window_title_without_a_game() -> None:
    from palamede_chessnut.app import title

    assert title(new_lobby()) == "Échiquier"
    assert title(new_lobby(after=finished_bridge())) == "Échiquier — 009"


def test_a_game_s_bridge_has_no_lobby_text() -> None:
    state = snapshot(finished_bridge())
    assert state["lobby"] is None and state["tips"] == {}


def test_the_screen_s_board_says_what_each_knight_square_chooses() -> None:
    """Smoke of Stéphane, 04/11 : the knight's gesture was not found from the text alone."""
    assert lobby.tips() == {
        "a3": "3′", "b3": "5′", "c3": "10′", "d3": "15′", "e3": "25′", "f3": "90′",
        "a4": "+0", "b4": "+2", "c4": "+3", "d4": "+5", "e4": "+10", "f4": "+30",
    }  # fmt: skip
    for square, value in lobby.tips().items():
        for home in lobby.KNIGHT_HOMES:
            (picked,) = choices(knight(home, chess.parse_square(square)))
            assert str(picked[1]) == value.strip("+′"), square


def test_no_clock_is_read_without_a_game() -> None:
    page = PageServer(new_lobby())
    asked: list[str] = []
    page.clocks.view = lambda _v, game_id, _t: asked.append(game_id)  # type: ignore[method-assign]
    page.start()
    try:
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", page.port)
        conn.request("GET", "/state", headers={"Host": f"127.0.0.1:{page.port}"})
        state = json.loads(conn.getresponse().read())
    finally:
        page.shutdown()
        page.server_close()
    assert state["clock"] is None and asked == []


# ---- the launch ------------------------------------------------------------


class FakeViewer:
    def __init__(self, monkeypatch, in_progress: list | None = None) -> None:
        self.in_progress = in_progress or []
        monkeypatch.setattr(viewer, "get_with_token", self.get_with_token)
        monkeypatch.setattr(viewer, "get_json", lambda _url: {"in_progress": self.in_progress})
        monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", TOKEN)
        monkeypatch.setenv("PALAMEDE_CONFIG", "/nonexistent/config.toml")
        monkeypatch.setattr(config, "named_by_environment", lambda: False)

    def get_with_token(self, url: str, _token: str):
        if url.endswith("/api/me"):
            return 200, {"player": "stephane", "kind": "human"}
        return 200, {"player": "stephane", "colors": ["white"]}


def test_the_app_on_a_board_without_a_game_waits_in_a_lobby(monkeypatch) -> None:
    FakeViewer(monkeypatch)
    bridge = bridge_from_args(["--viewer", VIEWER], "t", app=True)
    assert isinstance(bridge, Lobby)
    assert bridge.account == "stephane" and bridge.token_light == "on"
    assert bridge.new_game == config.NEW_GAME


@pytest.mark.parametrize(("argv", "app"), [([], False), (["--no-board"], True)])
def test_without_a_board_or_a_window_no_game_still_exits(monkeypatch, argv, app) -> None:
    FakeViewer(monkeypatch)
    with pytest.raises(SystemExit) as refused:
        bridge_from_args(["--viewer", VIEWER, *argv], "t", app=app)
    assert "no game in progress for stephane" in str(refused.value)


def test_a_game_in_progress_is_still_followed(monkeypatch) -> None:
    FakeViewer(monkeypatch, [{"game_id": "009", "white": "stephane", "black": "bot"}])
    bridge = bridge_from_args(["--viewer", VIEWER], "t", app=True)
    assert not isinstance(bridge, Lobby) and bridge.game_id == "009"


# ---- the session -----------------------------------------------------------


def until(condition, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def runner(bridge: Bridge) -> None:
    """A game's bridge over at once ; a lobby waits to be stopped."""
    if isinstance(bridge, Lobby):
        bridge.stopped.wait(5)
        return
    bridge.finished = True
    bridge.stopped.set()


def test_a_board_s_game_over_hands_the_board_to_a_lobby() -> None:
    done = finished_bridge()
    done.finished = False
    done.new_game = {"opponent": "bot-maia-1500", "time_control": "3+2"}
    changes: list[Bridge] = []
    session = Session(done, runner, changes.append)
    session.start()
    try:
        assert until(lambda: isinstance(session.bridge, Lobby))
        new = session.bridge
        assert isinstance(new, Lobby) and new.game_id == "009" and new.finished
        assert new.new_game == done.new_game and new._token == TOKEN
        assert new.on_created == session.open_game
        assert until(lambda: changes == [new])
    finally:
        session.close()


def test_a_mouse_game_over_keeps_its_bridge() -> None:
    done = Bridge(VIEWER, "009", chess.WHITE, TOKEN, with_board=False)
    session = Session(done, runner)
    session.start()
    assert session.worker is not None
    session.worker.join(2)
    time.sleep(0.05)
    assert session.bridge is done


def test_no_lobby_once_the_window_is_closed() -> None:
    done = finished_bridge()
    session = Session(done, lambda _bridge: None)
    session.closed = True
    session._idle(done)
    assert session.bridge is done


def test_a_lobby_cannot_switch_to_the_mouse() -> None:
    session = Session(new_lobby(), lambda bridge: bridge.stopped.wait(5))
    session.start()
    try:
        assert session.switch_board(False) == (409, NO_GAME)
    finally:
        session.close()


def test_the_lobby_s_finished_game_may_be_opened_again(monkeypatch) -> None:
    monkeypatch.setattr(
        Bridge, "seat", lambda _self: (200, {"player": "stephane", "colors": ["black"]})
    )
    session = Session(new_lobby(after=finished_bridge()), lambda bridge: bridge.stopped.wait(5))
    session.start()
    try:
        assert session.open_game("009") == (200, "")
        assert not isinstance(session.bridge, Lobby) and session.bridge.game_id == "009"
        assert session.bridge.player == chess.BLACK
    finally:
        session.close()


def test_closing_a_lobby_declares_nothing(monkeypatch) -> None:
    declared: list[object] = []
    monkeypatch.setattr(lobby.Bridge, "seat", lambda _self: (None, None))
    from palamede_chessnut import clock_hold

    monkeypatch.setattr(clock_hold, "declare", lambda *args: declared.append(args))
    session = Session(new_lobby(), lambda bridge: bridge.stopped.wait(5))
    session.start()
    session.close()
    assert declared == []


# ---- the viewer's answer -----------------------------------------------------


def test_post_json_reads_a_refusal_s_detail(monkeypatch) -> None:
    body = json.dumps({"detail": "Adversaire inconnu : bot-x"}).encode()

    class Opener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.HTTPError("u", 422, "Unprocessable", {}, io.BytesIO(body))

    monkeypatch.setattr(viewer, "_OPENER", Opener())
    assert viewer.post_json(f"{VIEWER}/api/games", TOKEN, {}) == (
        422,
        {"detail": "Adversaire inconnu : bot-x"},
    )


def test_post_json_unreachable(monkeypatch) -> None:
    class Opener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.URLError("down")

    monkeypatch.setattr(viewer, "_OPENER", Opener())
    assert viewer.post_json(f"{VIEWER}/api/games", TOKEN, {}) == (None, None)


def test_post_json_keeps_the_status_of_an_unreadable_success(monkeypatch) -> None:
    class Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class Opener:
        def open(self, request, **_kwargs):
            assert "Authorization" not in request.headers  # unredirected only
            assert request.unredirected_hdrs["Authorization"] == f"Bearer {TOKEN}"
            return Response(b"not json")

    monkeypatch.setattr(viewer, "_OPENER", Opener())
    assert viewer.post_json(f"{VIEWER}/api/games", TOKEN, {}) == (200, None)


# ---- the settings ------------------------------------------------------------


def test_the_settings_give_the_new_game(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('opponent = "bot-maia-1500"\ntime_control = "10+5"\n', encoding="utf-8")
    assert config.new_game(config.load(path)) == {
        "opponent": "bot-maia-1500",
        "time_control": "10+5",
    }
    assert config.new_game({}) == {"opponent": "bot-maia-1100", "time_control": "15+10"}


@pytest.mark.parametrize("value", ["5 + 3", "5", "blitz", "5+3+1", "1234+0"])
def test_a_clock_the_viewer_would_refuse_is_refused_here(tmp_path, value) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f'time_control = "{value}"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="time_control must be"):
        config.load(path)


async def test_a_corner_emptied_during_an_animation_keeps_its_time(monkeypatch) -> None:
    # A knight's blink is running when a1 leaves : h1 three seconds later is putting away.
    server = Server(monkeypatch)
    board, write = new_lobby(), Board()
    now = time.monotonic()
    board.animation = [frozenset()] * 2
    at(board, lifted(chess.A1), now - 10)
    await board.tick(write)
    await board.tick(write)
    at(board, lifted(chess.A1, chess.H1), now - 7)
    await board.tick(write)
    assert server.posts == [] and board.counting is None


@pytest.mark.parametrize(("gap", "opened"), [(lobby.LINK_GAP_SECONDS + 3, False), (0.1, True)])
async def test_corners_emptied_while_the_link_was_lost_open_nothing(
    monkeypatch, gap, opened
) -> None:
    # a1 put away, then h1 three seconds later, the board unlinked : seen at once on its
    # return. Two real ticks on a fake clock : the gap is measured, not set (review Junior #214).
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    server = Server(monkeypatch)
    board, write = new_lobby(), Board()
    at(board, chess.BaseBoard(), clock[0] - 10)
    await board.tick(write)
    clock[0] += gap
    at(board, lifted(chess.A1, chess.H1), clock[0] - GESTURE_SECONDS - 0.1)
    await board.tick(write)
    await board.tick(write)
    assert bool(server.posts) is opened
