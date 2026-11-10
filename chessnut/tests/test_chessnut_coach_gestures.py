"""The coach's gestures and the board's sound code (#186, step 2)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import chess
import pytest
from palamede_chessnut import coach_gestures, leds, sounds, viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.clock_choice import CHOICE_SECONDS
from palamede_chessnut.coach_gestures import EXERCISE, LESSON, pair_gesture, start_exercise
from palamede_chessnut.lobby import GESTURE_SECONDS
from test_chessnut_lobby import (
    TOKEN,
    VIEWER,
    Board,
    Server,
    held,
    knight,
    lifted,
    new_lobby,
    two_knights,
)

EXO = "exo.stephane.20261107-230000-abcd"
PROGRESS = f"{VIEWER}/api/coach/progress"
START = f"{VIEWER}/api/coach/exercises/e1/start"


class Coach:
    """The coach's two calls : the suggestion read, the exercise started."""

    def __init__(
        self,
        monkeypatch,
        *,
        progress: tuple[int | None, Any] = (200, {"suggestion": {"id": "e1", "kind": "find_move"}}),
        start: tuple[int | None, Any] = (201, {"game_id": EXO, "exercise": {"side": "black"}}),
    ) -> None:
        self.progress, self.start = progress, start
        self.calls: list[tuple[str, str]] = []
        monkeypatch.setattr(viewer, "get_with_token", self.get)
        monkeypatch.setattr(viewer, "post_json", self.post)

    def get(self, url: str, token: str, timeout: float = 10):
        self.calls.append(("GET", url))
        assert token == TOKEN
        return self.progress

    def post(self, url: str, token: str, payload: dict):
        self.calls.append(("POST", url))
        assert token == TOKEN
        return self.start


# ---- the sound code -------------------------------------------------------


def test_a_beep_is_the_sdk_s_command() -> None:
    """``0b 04``, then 2000 Hz and 80 ms, high byte first (EasyLinkSDK ``beep``)."""
    assert sounds.beep(2000, 80) == bytes([0x0B, 0x04, 0x07, 0xD0, 0x00, 0x50])


@pytest.mark.parametrize(
    ("series", "heard"),
    [
        ("ok", [(2000, 80), (2000, 80)]),
        ("no", [(1000, 80), (1000, 80)]),
        ("question", [(1000, 80), (2000, 80)]),
    ],
)
def test_the_series_of_the_owner_s_code(series, heard) -> None:
    frames = sounds.frames(series)
    beeps = [f for f in frames if f is not None]
    assert beeps == [sounds.beep(*b) for b in heard]
    # One silent tick between two beeps, nothing after the last one.
    assert frames == [beeps[0], None, beeps[1]]


async def test_the_board_s_loop_plays_one_entry_per_tick() -> None:
    bridge = Bridge(VIEWER, "g1", chess.WHITE, TOKEN)
    board = Board()
    bridge.say("no")
    for _ in range(4):
        await bridge._beep(board)
    assert board.frames == [sounds.beep(1000, 80)] * 2
    assert bridge.sound == []


# ---- the gestures -----------------------------------------------------------


@pytest.mark.parametrize(
    ("placement", "gesture"),
    [
        (lifted(chess.C1, chess.F1), EXERCISE),
        (lifted(chess.B1, chess.G1), LESSON),
        (lifted(chess.C1), None),
        (lifted(chess.C8, chess.F8), None),  # the black bishops
        (lifted(chess.C1, chess.F1, chess.E2), None),  # another piece off
        (lifted(chess.B1, chess.C1), None),  # the notches of #189 are gone
    ],
)
def test_exactly_one_pair_lifted(placement, gesture) -> None:
    assert pair_gesture(placement) == gesture


async def test_the_bishops_held_open_the_suggested_exercise(monkeypatch) -> None:
    coach = Coach(monkeypatch)
    board = new_lobby()
    held(board, lifted(chess.C1, chess.F1), GESTURE_SECONDS)
    await board.tick(Board())
    assert coach.calls == [("GET", PROGRESS), ("POST", START)]
    # Handed over as a new game, from the player's side in the exercise.
    assert board.created == EXO and board.player == chess.BLACK
    assert board.animation == leds.chaser(7)
    assert board.sound == sounds.frames("ok")


async def test_before_5_s_the_bishops_blink_and_the_window_counts(monkeypatch) -> None:
    coach = Coach(monkeypatch)
    board = new_lobby()
    held(board, lifted(chess.C1, chess.F1), 1.0)
    await board.tick(Board())
    assert coach.calls == []
    assert board.leds == leds.blink(frozenset({chess.C1, chess.F1}), 1.0)
    assert board.lobby_text() == "Exercice dans 4 s…"


async def test_a_disarmed_lobby_ignores_the_bishops(monkeypatch) -> None:
    coach = Coach(monkeypatch)
    board = new_lobby(armed=False)
    held(board, lifted(chess.C1, chess.F1), GESTURE_SECONDS)
    await board.tick(Board())
    assert coach.calls == [] and board.created is None


async def test_the_lobby_stays_armed_while_the_bishops_are_lifted(monkeypatch) -> None:
    """One bishop then the other : the lobby must not disarm in between."""
    Coach(monkeypatch)
    board = new_lobby()
    held(board, lifted(chess.F1), 1.0)
    await board.tick(Board())
    assert board.armed


@pytest.mark.parametrize(
    ("progress", "start", "said"),
    [
        ((200, {"suggestion": None}), None, coach_gestures.NONE_WAITING),
        ((None, None), None, coach_gestures.NO_SERVER),
        ((401, None), None, coach_gestures.REFUSALS[401]),
        (
            None,
            (409, {"detail": "Exercice déjà résolu"}),
            "Exercice non ouvert : Exercice déjà résolu",
        ),
        (None, (201, None), "Exercice non ouvert (HTTP 201)."),
    ],
)
async def test_an_exercise_not_opened_is_said_with_two_low_beeps(
    monkeypatch, progress, start, said
) -> None:
    kwargs = {k: v for k, v in (("progress", progress), ("start", start)) if v is not None}
    Coach(monkeypatch, **kwargs)
    board = new_lobby()
    held(board, lifted(chess.C1, chess.F1), GESTURE_SECONDS)
    await board.tick(Board())
    assert board.created is None and not board.creating
    assert board.lobby_text() == said
    assert board.sound == sounds.frames("no")


async def test_a_refused_exercise_is_not_asked_again_while_the_bishops_stay(monkeypatch) -> None:
    # The same placement is tried once : no request storm at ten ticks a second.
    coach = Coach(monkeypatch, progress=(503, {}))
    board = new_lobby()
    held(board, lifted(chess.C1, chess.F1), GESTURE_SECONDS)
    await board.tick(Board())
    asked = list(coach.calls)
    assert asked and board.created is None
    await board.tick(Board())
    await board.tick(Board())
    assert coach.calls == asked


async def test_the_knights_held_say_the_lessons_are_coming(monkeypatch) -> None:
    coach = Coach(monkeypatch)
    board = new_lobby()
    held(board, lifted(chess.B1, chess.G1), GESTURE_SECONDS)
    await board.tick(Board())
    assert coach.calls == []
    assert board.lobby_text() == coach_gestures.SOON
    assert board.sound == sounds.frames("no")


async def test_the_knights_lifted_less_than_5_s_count_the_lesson(monkeypatch) -> None:
    Coach(monkeypatch)
    board = new_lobby()
    held(board, lifted(chess.B1, chess.G1), 2.0)
    await board.tick(Board())
    assert board.lobby_text() == "Leçon du jour dans 3 s…"


async def test_once_the_pieces_are_back_the_message_goes(monkeypatch) -> None:
    Coach(monkeypatch)
    board = new_lobby()
    held(board, lifted(chess.B1, chess.G1), GESTURE_SECONDS)
    await board.tick(Board())
    held(board, chess.BaseBoard(), 1.0)
    await board.tick(Board())
    assert board.lobby_text() == "Aucune partie, cadence 15+10."


def test_start_exercise_quotes_the_id(monkeypatch) -> None:
    coach = Coach(monkeypatch, progress=(200, {"suggestion": {"id": "a/b"}}))
    assert start_exercise(VIEWER, TOKEN)[0] == EXO
    assert coach.calls[1] == ("POST", f"{VIEWER}/api/coach/exercises/a%2Fb/start")


def test_an_exercise_without_a_side_is_played_as_white(monkeypatch) -> None:
    Coach(monkeypatch, start=(201, {"game_id": EXO}))
    assert start_exercise(VIEWER, TOKEN) == (EXO, chess.WHITE, None)


# ---- the beeps of the gestures already there --------------------------------


async def test_a_new_game_by_the_corners_beeps_ok(monkeypatch) -> None:
    Server(monkeypatch)
    board = new_lobby()
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS)
    await board.tick(Board())
    assert board.sound == sounds.frames("ok")


async def test_a_new_game_refused_beeps_no(monkeypatch) -> None:
    Server(monkeypatch, status=429, body={"detail": "trop"})
    board = new_lobby()
    held(board, lifted(chess.A1, chess.H1), GESTURE_SECONDS)
    await board.tick(Board())
    assert board.sound == sounds.frames("no")


async def test_a_clock_chosen_beeps_ok(monkeypatch) -> None:
    Server(monkeypatch)
    board = new_lobby()
    held(board, knight(chess.G1, chess.F3), CHOICE_SECONDS)
    await board.tick(Board())
    assert board.sound == sounds.frames("ok")


# ---- the exercise's answer --------------------------------------------------


def _exercise_bridge(monkeypatch, answer: tuple[str | None, bool]) -> Bridge:
    bridge = Bridge(VIEWER, EXO, chess.WHITE, TOKEN)
    bridge.game = chess.Board()
    bridge.exercise = {"kind": "find_move", "motif": "missed_mate"}
    monkeypatch.setattr(bridge, "post_move", lambda uci: answer)
    return bridge


@pytest.mark.parametrize(
    ("answer", "series"),
    [((None, False), "ok"), (("Ce n'est pas le coup", True), "no")],
)
def test_an_exercise_s_move_found_or_not_is_heard(monkeypatch, answer, series) -> None:
    bridge = _exercise_bridge(monkeypatch, answer)
    asyncio.run(bridge._play(chess.Move.from_uci("e2e4")))
    assert bridge.sound == sounds.frames(series)


def test_a_move_not_delivered_says_nothing(monkeypatch) -> None:
    bridge = _exercise_bridge(monkeypatch, ("timeout", False))
    asyncio.run(bridge._play(chess.Move.from_uci("e2e4")))
    assert bridge.sound == []


def test_a_game_s_move_is_silent(monkeypatch) -> None:
    bridge = _exercise_bridge(monkeypatch, (None, False))
    bridge.exercise = None
    asyncio.run(bridge._play(chess.Move.from_uci("e2e4")))
    assert bridge.sound == []


async def test_a_knight_s_count_replaces_the_lessons_message(monkeypatch) -> None:
    # Both knights lifted 5 s : « soon ». The b1 knight then put on a3, g1 still lifted :
    # the window counts the clock, the refusal is gone.
    Coach(monkeypatch)
    board, write = new_lobby(), Board()
    held(board, lifted(chess.B1, chess.G1), GESTURE_SECONDS)
    await board.tick(write)
    assert board.lobby_text() == coach_gestures.SOON
    held(board, two_knights(chess.A3, None), 0.4)
    await board.tick(write)
    assert board.lobby_text() == "Cadence 3+10 dans 2 s…"


async def test_the_message_stays_while_the_knights_are_still_lifted(monkeypatch) -> None:
    Coach(monkeypatch)
    board = new_lobby()
    held(board, lifted(chess.B1, chess.G1), GESTURE_SECONDS)
    await board.tick(Board())
    await board.tick(Board())  # the same placement, already tried
    assert board.lobby_text() == coach_gestures.SOON


def test_a_refusal_carrying_a_game_id_opens_nothing(monkeypatch) -> None:
    Coach(monkeypatch, start=(500, {"game_id": EXO}))
    assert start_exercise(VIEWER, TOKEN) == (
        None,
        chess.WHITE,
        "Exercice non ouvert (HTTP 500).",
    )


def test_a_game_s_refused_move_is_silent(monkeypatch) -> None:
    """The guard before « no » too (review Junior #293) : only an exercise beeps."""
    bridge = _exercise_bridge(monkeypatch, ("Coup refusé (ILLEGAL)", True))
    bridge.exercise = None
    asyncio.run(bridge._play(chess.Move.from_uci("e2e4")))
    assert bridge.sound == [] and bridge.refused is not None


async def test_the_series_sent_is_in_the_log(monkeypatch, caplog) -> None:
    """S2 step 4 (08/11) : two high beeps heard where « no » is sent ; the log says which."""
    Coach(monkeypatch)
    board = new_lobby()
    held(board, lifted(chess.B1, chess.G1), GESTURE_SECONDS)
    with caplog.at_level(logging.INFO):
        await board.tick(Board())
    assert [r.getMessage() for r in caplog.records if r.getMessage().startswith("sound")] == [
        "sound no"
    ]
