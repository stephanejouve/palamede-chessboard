"""The game's clock in the window (#135) : read from the viewer, what is left to each side."""

from __future__ import annotations

import json

import pytest
from palamede_chessnut import clock as clock_module
from palamede_chessnut import viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.clock import ClockCache, seconds_left
from palamede_chessnut.page import PageServer

TOKEN = "secret-token-never-shown"
VIEWER = "https://viewer.test"
GAME = "tablier.stephane.20261029-211945-e6e0"


def clock(**changes) -> dict:
    """A viewer clock, White to move, read at ``now_ts`` 1000, last move at 990."""
    base = {
        "game_id": GAME,
        "white_seconds": 300.0,
        "black_seconds": 290.0,
        "increment_seconds": 3,
        "active": "white",
        "last_move_ts": 990.0,
        "started": True,
        "flag_fall": None,
        "now_ts": 1000.0,
    }
    return {**base, **changes}


# ---- what is left to each side ----------------------------------------------


def test_the_side_to_move_has_its_time_running() -> None:
    assert seconds_left(clock()) == {
        "white": 290.0,
        "black": 290.0,
        "active": "white",
        "flag": None,
        "started": True,
    }


def test_the_time_since_the_read_counts_too() -> None:
    assert seconds_left(clock(), age=2.5)["white"] == pytest.approx(287.5)


def test_before_the_first_move_nothing_runs() -> None:
    view = seconds_left(clock(started=False, active=None, last_move_ts=None))
    assert (view["white"], view["black"], view["active"], view["started"]) == (
        300,
        290,
        None,
        False,
    )


def test_a_fallen_flag_stops_the_clock() -> None:
    view = seconds_left(clock(flag_fall="white", white_seconds=0.0))
    assert (view["white"], view["active"], view["flag"]) == (0.0, None, "white")


def test_the_end_of_the_game_stops_the_clock() -> None:
    assert seconds_left(clock(active=None))["active"] is None


def test_never_below_zero() -> None:
    assert seconds_left(clock(), age=10_000)["white"] == 0.0


@pytest.mark.parametrize(
    "answer",
    [None, "x", {}, clock(white_seconds="beaucoup"), clock(now_ts=None)],
    ids=["sans pendule", "pas un objet", "vide", "secondes illisibles", "sans now_ts"],
)
def test_no_clock_or_unreadable(answer) -> None:
    assert seconds_left(answer) is None


# ---- one read of the viewer per second at most ------------------------------


class FakeViewer:
    def __init__(self, monkeypatch) -> None:
        self.answer: object = {"game_id": GAME, "clock": clock()}
        self.asked: list[tuple[str, float]] = []
        monkeypatch.setattr(viewer, "get_json", self.get_json)

        def no_token(*_args, **_kwargs):
            raise AssertionError("the clock is read without the token")

        monkeypatch.setattr(viewer, "get_with_token", no_token)

    def get_json(self, url: str, timeout: float = 10):
        self.asked.append((url, timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def fake(monkeypatch) -> FakeViewer:
    return FakeViewer(monkeypatch)


class Monotonic:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_read_once_per_refresh_and_counted_down_in_between(fake) -> None:
    now = Monotonic()
    cache = ClockCache(now)
    assert cache.view(VIEWER, GAME)["white"] == 290.0
    now.t += 0.4
    assert cache.view(VIEWER, GAME)["white"] == pytest.approx(289.6)
    assert len(fake.asked) == 1
    now.t += clock_module.REFRESH
    cache.view(VIEWER, GAME)
    assert len(fake.asked) == 2
    assert fake.asked[0] == (f"{VIEWER}/api/clock?game_id={GAME}", clock_module.TIMEOUT)


def test_another_game_is_read_at_once(fake) -> None:
    now = Monotonic()
    cache = ClockCache(now)
    cache.view(VIEWER, GAME)
    cache.view(VIEWER, "008d")
    assert [url for url, _ in fake.asked][-1] == f"{VIEWER}/api/clock?game_id=008d"


def test_an_unreachable_viewer_keeps_the_last_clock_running(fake) -> None:
    now = Monotonic()
    cache = ClockCache(now)
    cache.view(VIEWER, GAME)
    fake.answer = OSError("down")
    now.t += clock_module.REFRESH
    view = cache.view(VIEWER, GAME)
    # The last clock, counted down from its own read.
    assert view["white"] == pytest.approx(290.0 - clock_module.REFRESH)


def test_an_unreachable_viewer_is_tried_once_per_refresh(fake) -> None:
    """Not at every poll of the page : a down viewer must not hold /state."""
    now = Monotonic()
    cache = ClockCache(now)
    fake.answer = OSError("down")
    for _ in range(5):
        cache.view(VIEWER, GAME)
        now.t += 0.1
    assert len(fake.asked) == 1


def test_no_clock_of_the_previous_game_on_another_game(fake) -> None:
    now = Monotonic()
    cache = ClockCache(now)
    cache.view(VIEWER, GAME)
    fake.answer = OSError("down")
    assert cache.view(VIEWER, "008d") is None


def test_any_error_of_the_read_never_breaks_the_state(fake) -> None:
    fake.answer = RuntimeError("anything")
    assert ClockCache(Monotonic()).view(VIEWER, GAME) is None


def test_a_game_without_clock(fake) -> None:
    fake.answer = {"game_id": GAME, "clock": None}
    assert ClockCache(Monotonic()).view(VIEWER, GAME) is None


def test_the_game_id_is_quoted(fake) -> None:
    ClockCache(Monotonic()).view(VIEWER, "a b&c")
    assert fake.asked[0][0] == f"{VIEWER}/api/clock?game_id=a%20b%26c"


# ---- in the page's state ------------------------------------------------------


@pytest.fixture
def page():
    server = PageServer(Bridge(VIEWER, GAME, True, TOKEN))
    server.start()
    yield server
    server.shutdown()
    server.server_close()


def test_the_state_carries_the_clock(page, fake) -> None:
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", page.port, timeout=5)
    connection.request("GET", "/state")
    response = connection.getresponse()
    body = response.read()
    state = json.loads(body)
    assert state["clock"]["active"] == "white"
    assert state["clock"]["black"] == 290.0
    assert TOKEN.encode() not in body
