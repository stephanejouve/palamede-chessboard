"""« Cases attaquées » answered on the board (#237, step 3)."""

from __future__ import annotations

from pathlib import Path

import chess
import pytest
from palamede_chessnut import designate, exercise, leds, viewer
from palamede_chessnut.bridge import RESIGN_SECONDS, Bridge
from palamede_chessnut.designate import KING_HOLD_SECONDS, MIN_LIFT_SECONDS, Designation
from palamede_chessnut.protocol import led_frame
from palamede_chessnut.status import snapshot as page_state
from test_chessnut import after, frame, placement, ready_bridge

START = chess.BaseBoard()
TAP = MIN_LIFT_SECONDS + 0.1
DARK = {led_frame(frozenset())}


def without(*squares: chess.Square) -> chess.BaseBoard:
    board = START.copy()
    for square in squares:
        board.remove_piece_at(square)
    return board


class Hand:
    """Plays gestures on a :class:`Designation`, on its own clock."""

    def __init__(self) -> None:
        self.designation = Designation()
        self.now = 100.0
        self.validated = 0

    def see(self, board: chess.BaseBoard, wait: float = 0.0) -> None:
        self.now += wait
        if self.designation.feed(START, board, chess.WHITE, self.now):
            self.validated += 1

    def tap(self, square: chess.Square, held: float = TAP, gap: float = 0.1) -> None:
        self.see(without(square), gap)
        self.see(START, held)

    def names(self) -> list[str]:
        return self.designation.names()


def test_a_piece_lifted_then_put_back_designates_its_square() -> None:
    hand = Hand()
    hand.tap(chess.E2)
    assert hand.names() == ["e2"] and hand.validated == 0


def test_the_same_gesture_takes_it_back() -> None:
    hand = Hand()
    hand.tap(chess.E2)
    hand.tap(chess.E2)
    assert hand.names() == []


def test_a_piece_stays_designated_while_others_are() -> None:
    hand = Hand()
    hand.tap(chess.E2)
    hand.tap(chess.B1)
    assert hand.names() == ["b1", "e2"]


def test_a_flicker_designates_nothing() -> None:
    hand = Hand()
    hand.tap(chess.E2, held=MIN_LIFT_SECONDS / 2)
    assert hand.names() == []


def test_the_opponent_s_pieces_designate_nothing() -> None:
    hand = Hand()
    hand.tap(chess.E7)
    assert hand.names() == []


def test_two_pieces_up_designate_nothing_until_the_board_matches() -> None:
    hand = Hand()
    hand.see(without(chess.E2), 0.1)
    hand.see(without(chess.E2, chess.D2), TAP)
    hand.see(without(chess.E2), TAP)  # one back : still the same broken gesture
    hand.see(START, TAP)
    assert hand.names() == []
    hand.tap(chess.D2)  # the board has matched : gestures count again
    assert hand.names() == ["d2"]


def test_a_move_under_way_designates_nothing() -> None:
    hand = Hand()
    moved = without(chess.E2)
    moved.set_piece_at(chess.E4, chess.Piece(chess.PAWN, chess.WHITE))
    hand.see(without(chess.E2), 0.1)
    hand.see(moved, TAP)
    hand.see(START, TAP)
    assert hand.names() == []


def test_a_piece_put_on_another_square_of_the_same_kind_breaks_the_gesture() -> None:
    hand = Hand()
    other = START.copy()
    other.set_piece_at(chess.E2, chess.Piece(chess.KNIGHT, chess.WHITE))
    hand.see(other, 0.1)
    hand.see(START, TAP)
    assert hand.names() == []


def test_the_king_twice_validates() -> None:
    hand = Hand()
    hand.tap(chess.E1)
    assert hand.validated == 0
    hand.tap(chess.E1)
    assert hand.validated == 1
    assert hand.names() == []  # the king is no answer


def test_the_validation_counts_afresh_after_it_fired() -> None:
    hand = Hand()
    for _ in range(3):
        hand.tap(chess.E1)
    assert hand.validated == 1


def test_a_king_held_too_long_is_no_validation() -> None:
    hand = Hand()
    hand.tap(chess.E1)
    hand.tap(chess.E1, held=KING_HOLD_SECONDS)
    assert hand.validated == 0


def test_a_long_hold_does_not_count_as_the_first_lift() -> None:
    hand = Hand()
    hand.tap(chess.E1, held=KING_HOLD_SECONDS + 1)
    hand.tap(chess.E1)
    assert hand.validated == 0


def test_another_piece_lifted_without_the_board_matching_designates_nothing() -> None:
    hand = Hand()
    hand.see(without(chess.E2), 0.1)
    hand.see(without(chess.D2), TAP)
    hand.see(START, TAP)
    assert hand.names() == []


def test_the_delay_runs_between_the_lifts_not_during_the_second() -> None:
    # Smoke of #308 (09/11) : lifted at 16:01:12 and 16:01:14, nothing sent.
    hand = Hand()
    hand.tap(chess.E1, held=1.0)
    hand.see(without(chess.E1), 1.0)
    hand.see(without(chess.E1), 1.0)  # still in the air, 2 s after the first put back
    hand.see(without(chess.E1), 0.2)
    hand.see(START, 0.2)
    assert hand.validated == 1


def test_the_delay_starts_at_the_put_back_not_at_the_lift() -> None:
    # Review Junior #308 : a normal gesture, lifted 1 s then paused 1.5 s.
    hand = Hand()
    hand.tap(chess.E1, held=1.0)
    hand.tap(chess.E1, gap=1.5)
    assert hand.validated == 1


def test_two_lifts_too_far_apart_do_not_validate() -> None:
    hand = Hand()
    hand.tap(chess.E1)
    hand.tap(chess.E1, gap=KING_HOLD_SECONDS + 0.1)
    assert hand.validated == 0


def test_a_piece_between_the_two_lifts_starts_the_validation_over() -> None:
    hand = Hand()
    hand.tap(chess.E1)
    hand.tap(chess.E2)
    hand.tap(chess.E1)
    assert hand.validated == 0 and hand.names() == ["e2"]


# ---- on the bridge --------------------------------------------------------


def attacked_bridge(kind: str = "attacked") -> Bridge:
    bridge = ready_bridge()
    bridge.game_id = "exo.p.1"
    bridge.exercise = {"id": "ab/1", "kind": kind, "motif": ""}
    bridge.on_frame(None, bytearray(frame(placement(chess.Board()))))
    return bridge


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr("palamede_chessnut.bridge.time.monotonic", lambda: now[0])
    return now


class Board:
    def __init__(self, bridge: Bridge, clock: list[float]) -> None:
        self.bridge, self.clock = bridge, clock
        self.written: list[bytes] = []

    async def write(self, command: bytes) -> None:
        self.written.append(command)

    async def show(self, placed: chess.BaseBoard, wait: float) -> None:
        self.clock[0] += wait
        self.bridge.on_frame(None, bytearray(frame(placed)))
        await self.bridge.tick(self.write)

    async def tap(self, square: chess.Square) -> None:
        await self.show(without(square), 0.1)
        await self.show(START, TAP)


@pytest.fixture
def posted(monkeypatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    def answer(url: str, token: str, payload: dict) -> tuple[int, dict]:
        calls.append((url, payload))
        return 200, {"status": "ok"}

    monkeypatch.setattr(viewer, "post_json", answer)
    return calls


async def test_the_set_is_posted_once_validated(clock, posted) -> None:
    bridge = attacked_bridge()
    board = Board(bridge, clock)
    await board.tap(chess.G1)
    await board.tap(chess.E2)
    assert posted == []
    await board.tap(chess.E1)
    await board.tap(chess.E1)
    assert posted == [
        ("https://viewer.test/api/coach/exercises/ab%2F1/answer", {"squares": ["e2", "g1"]})
    ]


async def test_an_empty_set_is_a_legitimate_answer(clock, posted) -> None:
    board = Board(attacked_bridge(), clock)
    await board.tap(chess.E1)
    await board.tap(chess.E1)
    assert posted[0][1] == {"squares": []}


async def test_no_beep_while_designating(clock, posted) -> None:
    bridge = attacked_bridge()
    board = Board(bridge, clock)
    await board.tap(chess.E2)
    await board.tap(chess.E1)
    assert bridge.sound == []


async def test_a_right_answer_beeps_ok_and_blinks_its_squares(clock, posted) -> None:
    bridge = attacked_bridge()
    board = Board(bridge, clock)
    said: list[str] = []
    bridge.say = said.append  # type: ignore[method-assign]
    await board.tap(chess.E2)
    await board.tap(chess.E1)
    await board.tap(chess.E1)
    assert said == ["ok"]
    assert bridge.animation == leds.chosen(frozenset({chess.E2}))[1:]  # first frame shown
    assert bridge.designation.squares == set()


@pytest.mark.parametrize(
    ("status", "body", "sound", "note"),
    [
        (
            409,
            {"detail": "Ce ne sont pas les bonnes cases"},
            ["no"],
            "Ce ne sont pas les bonnes cases. Corrigez",
        ),
        (409, None, ["no"], "Réponse refusée. Corrigez"),
        (None, None, [], designate.NO_SERVER),
        (503, {"detail": "x"}, [], designate.NO_SERVER),
    ],
)
async def test_a_refused_answer_keeps_the_designations(
    clock, monkeypatch, status, body, sound, note
) -> None:
    monkeypatch.setattr(viewer, "post_json", lambda *_a: (status, body))
    bridge = attacked_bridge()
    board = Board(bridge, clock)
    said: list[str] = []
    bridge.say = said.append  # type: ignore[method-assign]
    await board.tap(chess.E2)
    await board.tap(chess.E1)
    await board.tap(chess.E1)
    assert said == sound
    assert bridge.designation.names() == ["e2"]
    assert page_state(bridge)["refused_text"].startswith(note)
    await board.tap(chess.D2)  # the next gesture clears what was said
    assert page_state(bridge)["refused_text"] is None


async def test_the_designated_squares_light_up_on_the_board_and_in_the_window(
    clock, posted
) -> None:
    bridge = attacked_bridge()
    board = Board(bridge, clock)
    await board.tap(chess.E2)
    await board.show(START, 1.0)
    assert board.written[-1] == led_frame(frozenset({chess.E2}))
    assert page_state(bridge)["designated"] == ["e2"]


def _spied(bridge: Bridge) -> tuple[list[str], list[str]]:
    moves: list[str] = []
    said: list[str] = []

    def post(uci: str) -> tuple[None, bool]:
        moves.append(uci)
        return None, False

    bridge.post_move = post  # type: ignore[method-assign]
    bridge.say = said.append  # type: ignore[method-assign]
    return moves, said


async def test_a_move_is_no_answer_it_lights_up_until_taken_back(clock, posted) -> None:
    # Smoke of #308 (09/11) : a piece moved was posted at once, then refused.
    bridge = attacked_bridge()
    moves, said = _spied(bridge)
    board = Board(bridge, clock)
    await board.show(placement(after("e4")), 0.1)
    await board.show(placement(after("e4")), 1.0)
    assert moves == [] and said == [] and posted == []
    assert board.written[-1] == led_frame(frozenset({chess.E2, chess.E4}))
    await board.show(START, 0.1)
    await board.show(START, 1.0)
    assert board.written[-1] == led_frame(frozenset())


async def test_a_find_move_still_posts_its_move(clock) -> None:
    bridge = attacked_bridge("find_move")
    moves, _said = _spied(bridge)
    board = Board(bridge, clock)
    await board.show(placement(after("e4")), 0.1)
    await board.show(placement(after("e4")), 1.0)
    assert moves == ["e2e4"]


@pytest.mark.parametrize("kind", ["find_move"])
async def test_a_find_move_designates_nothing(clock, posted, kind) -> None:
    bridge = attacked_bridge(kind)
    board = Board(bridge, clock)
    await board.tap(chess.E2)
    await board.tap(chess.E1)
    await board.tap(chess.E1)
    assert bridge.designation.names() == [] and posted == []


async def test_not_the_player_s_turn_designates_nothing(clock, posted) -> None:
    game = chess.Board()
    game.turn = chess.BLACK
    bridge = ready_bridge(game)
    bridge.exercise = {"id": "a", "kind": "attacked", "motif": ""}
    bridge.on_frame(None, bytearray(frame(placement(game))))
    await Board(bridge, clock).tap(chess.E2)
    assert bridge.designation.names() == []


def test_an_answer_without_a_known_id_stays_on_the_viewer() -> None:
    bridge = attacked_bridge()
    bridge.exercise = {"id": "", "kind": "attacked", "motif": ""}
    assert not designate.answers_on_board(bridge)


# ---- the king : validation first, resignation after -----------------------


async def test_in_an_exercise_the_countdown_waits_for_the_hold(clock, monkeypatch) -> None:
    resigned: list[bool] = []
    bridge = attacked_bridge()

    def resign() -> tuple[int, str]:
        resigned.append(True)
        return 200, ""

    monkeypatch.setattr(bridge, "_post_resign", resign)
    board = Board(bridge, clock)
    await board.show(without(chess.E1), 0.1)
    await board.show(without(chess.E1), KING_HOLD_SECONDS - 0.2)
    assert set(board.written) <= DARK  # no blink : it may still be a validation
    await board.show(without(chess.E1), 0.2)
    assert board.written[-1] == led_frame(leds.countdown(chess.E1, 0.0))
    await board.show(without(chess.E1), RESIGN_SECONDS - 0.2)
    assert resigned == []
    await board.show(without(chess.E1), 0.3)
    assert resigned == [True]


async def test_a_find_move_also_waits_for_the_hold(clock) -> None:
    board = Board(attacked_bridge("find_move"), clock)
    await board.show(without(chess.E1), 0.1)
    await board.show(without(chess.E1), KING_HOLD_SECONDS - 0.2)
    assert set(board.written) <= DARK


async def test_a_normal_game_counts_down_at_once(clock) -> None:
    bridge = ready_bridge()
    bridge.on_frame(None, bytearray(frame(placement(chess.Board()))))
    board = Board(bridge, clock)
    await board.show(without(chess.E1), 0.1)
    assert board.written[-1] == led_frame(leds.countdown(chess.E1, 0.0))


async def test_the_countdown_keeps_the_designated_squares_lit(clock, posted) -> None:
    bridge = attacked_bridge()
    board = Board(bridge, clock)
    await board.tap(chess.E2)
    await board.show(without(chess.E1), 0.1)
    await board.show(without(chess.E1), KING_HOLD_SECONDS)
    assert board.written[-1] == led_frame(frozenset({chess.E1, chess.E2}))


async def test_the_answer_s_blink_plays_before_the_end_s(clock, posted) -> None:
    bridge = attacked_bridge()
    board = Board(bridge, clock)
    await board.tap(chess.E2)
    await board.tap(chess.E1)
    await board.tap(chess.E1)
    chosen = list(bridge.animation)
    bridge.live = False
    await bridge.tick(board.write)
    assert bridge.finished
    assert bridge.animation[: len(chosen) - 1] == chosen[1:]
    assert bridge.animation[len(chosen) - 1 :] == leds.finished_blink()


def test_the_exercise_keeps_its_id(monkeypatch) -> None:
    listing = {"exercises": [{"id": "x", "kind": "attacked", "game_ids": ["exo.p.1"]}]}
    assert exercise.found(listing, "exo.p.1") == {"id": "x", "kind": "attacked", "motif": ""}


def test_the_window_draws_the_designated_squares() -> None:
    web = Path(designate.__file__).parent / "web"
    script = (web / "app.js").read_text("utf-8")
    assert "renderLeds(state.differing, state.designated);" in script
    assert "designated.map((square) => [square, 'led picked'])" in script
    assert "${designated.join()}" in script  # a change of the set redraws
    style = (web / "app.css").read_text("utf-8")
    assert "cg-board square.led.picked::after" in style
