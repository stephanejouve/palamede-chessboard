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
        #: The token each read carried : ``None`` for a public read.
        self.tokens: list[str | None] = []
        self.status: int | None = 200
        monkeypatch.setattr(viewer, "get_json", self.get_json)
        monkeypatch.setattr(viewer, "get_with_token", self.get_with_token)

    def get_json(self, url: str, timeout: float = 10):
        self.asked.append((url, timeout))
        self.tokens.append(None)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def get_with_token(self, url: str, token: str, timeout: float = 10):
        self.asked.append((url, timeout))
        self.tokens.append(token)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.status, self.answer if self.status == 200 else None


@pytest.fixture
def fake(monkeypatch) -> FakeViewer:
    return FakeViewer(monkeypatch)


class Monotonic:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def cache_without_thread(now: Monotonic) -> ClockCache:
    """The thread's step is :meth:`ClockCache.refresh`, called by hand here."""
    return ClockCache(now, thread=False)


def test_nothing_before_the_first_read_then_counted_down(fake) -> None:
    now = Monotonic()
    cache = cache_without_thread(now)
    assert cache.view(VIEWER, GAME) is None  # view() never reads
    assert fake.asked == []
    cache.refresh()
    assert cache.view(VIEWER, GAME)["white"] == 290.0
    now.t += 0.4
    assert cache.view(VIEWER, GAME)["white"] == pytest.approx(289.6)
    assert fake.asked == [(f"{VIEWER}/api/clock?game_id={GAME}", clock_module.TIMEOUT)]


def test_another_game_shows_nothing_until_read(fake) -> None:
    now = Monotonic()
    cache = cache_without_thread(now)
    cache.view(VIEWER, GAME)
    cache.refresh()
    assert cache.view(VIEWER, "008d") is None
    cache.refresh()
    assert fake.asked[-1][0] == f"{VIEWER}/api/clock?game_id=008d"


def test_an_unreachable_viewer_keeps_the_last_clock_running(fake) -> None:
    now = Monotonic()
    cache = cache_without_thread(now)
    cache.view(VIEWER, GAME)
    cache.refresh()
    fake.answer = OSError("down")
    now.t += 1.5
    cache.refresh()
    # The last clock, counted down from its own read.
    assert cache.view(VIEWER, GAME)["white"] == pytest.approx(288.5)


def test_no_clock_of_the_previous_game_on_another_game(fake) -> None:
    now = Monotonic()
    cache = cache_without_thread(now)
    cache.view(VIEWER, GAME)
    cache.refresh()
    fake.answer = OSError("down")
    cache.view(VIEWER, "008d")
    cache.refresh()
    assert cache.view(VIEWER, "008d") is None


def test_a_late_answer_for_a_game_left_is_dropped(fake, monkeypatch) -> None:
    """A read of the old game that ends after « Ouvrir » must not show its clock."""
    now = Monotonic()
    cache = cache_without_thread(now)
    cache.view(VIEWER, GAME)

    def answer_then_switch(url: str, timeout: float = 10):
        cache.view(VIEWER, "008d")  # the page moved to another game meanwhile
        return {"game_id": GAME, "clock": clock()}

    monkeypatch.setattr(viewer, "get_json", answer_then_switch)
    cache._read(VIEWER, GAME)
    assert cache.view(VIEWER, "008d") is None


def test_any_error_of_the_read_never_breaks_the_state(fake) -> None:
    fake.answer = RuntimeError("anything")
    cache = cache_without_thread(Monotonic())
    cache.view(VIEWER, GAME)
    cache.refresh()
    assert cache.view(VIEWER, GAME) is None


def test_a_game_without_clock(fake) -> None:
    fake.answer = {"game_id": GAME, "clock": None}
    cache = cache_without_thread(Monotonic())
    cache.view(VIEWER, GAME)
    cache.refresh()
    assert cache.view(VIEWER, GAME) is None


def test_the_game_id_is_quoted(fake) -> None:
    cache = cache_without_thread(Monotonic())
    cache.view(VIEWER, "a b&c")
    cache.refresh()
    assert fake.asked[0][0] == f"{VIEWER}/api/clock?game_id=a%20b%26c"


def test_the_thread_reads_once_per_refresh_and_stops(fake) -> None:
    import time

    cache = ClockCache()
    cache.view(VIEWER, GAME)
    deadline = time.monotonic() + 5
    while cache.view(VIEWER, GAME) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert cache.view(VIEWER, GAME) is not None
    reads = len(fake.asked)
    time.sleep(0.3)
    assert len(fake.asked) == reads  # not before REFRESH
    cache.close()
    cache._thread.join(5)
    assert not cache._thread.is_alive()


# ---- in the page's state ------------------------------------------------------


@pytest.fixture
def page():
    server = PageServer(Bridge(VIEWER, GAME, True, TOKEN))
    server.start()
    yield server
    server.shutdown()
    server.server_close()


def test_the_state_carries_the_clock(page, fake) -> None:
    """The first /state has no clock yet (the thread reads it), the next ones do."""
    import http.client
    import time

    state: dict = {}
    deadline = time.monotonic() + 5
    while state.get("clock") is None and time.monotonic() < deadline:
        connection = http.client.HTTPConnection("127.0.0.1", page.port, timeout=5)
        connection.request("GET", "/state")
        body = connection.getresponse().read()
        assert TOKEN.encode() not in body
        state = json.loads(body)
        time.sleep(0.02)
    assert state["clock"]["active"] == "white"
    assert state["clock"]["black"] == 290.0


def test_the_bridge_reads_the_clock_with_the_player_token(fake) -> None:
    """Owner, 31/10 : only a player's read ends the game on time, so the
    read carries the token (in the header, never in the URL)."""
    cache = cache_without_thread(Monotonic())
    cache.view(VIEWER, GAME, TOKEN)
    cache.refresh()
    assert fake.tokens == [TOKEN]
    assert fake.asked == [(f"{VIEWER}/api/clock?game_id={GAME}", clock_module.TIMEOUT)]
    assert cache.view(VIEWER, GAME, TOKEN)["white"] == 290.0


@pytest.mark.parametrize("status", [None, 401, 503])
def test_a_refused_read_keeps_the_last_clock(fake, status) -> None:
    now = Monotonic()
    cache = cache_without_thread(now)
    cache.view(VIEWER, GAME, TOKEN)
    cache.refresh()
    fake.status = status
    now.t += 1.5
    cache.refresh()
    assert cache.view(VIEWER, GAME, TOKEN)["white"] == pytest.approx(288.5)


def test_the_page_reads_the_clock_with_the_token(page, fake) -> None:
    import http.client
    import time

    deadline = time.monotonic() + 5
    while not fake.tokens and time.monotonic() < deadline:
        connection = http.client.HTTPConnection("127.0.0.1", page.port, timeout=5)
        connection.request("GET", "/state")
        connection.getresponse().read()
        time.sleep(0.02)
    assert fake.tokens and set(fake.tokens) == {TOKEN}


def test_a_slow_viewer_never_holds_the_state(page, monkeypatch) -> None:
    """Review Junior #139 : a viewer at 2 s, /state still answers at once."""
    import http.client
    import threading
    import time

    release = threading.Event()

    def slow(url: str, token: str, timeout: float = 10):
        release.wait(2)
        return 200, {"game_id": GAME, "clock": clock()}

    # The page's read carries the token : that is the read that must not hold /state.
    monkeypatch.setattr(viewer, "get_with_token", slow)
    try:
        for _ in range(4):
            start = time.monotonic()
            connection = http.client.HTTPConnection("127.0.0.1", page.port, timeout=5)
            connection.request("GET", "/state")
            assert connection.getresponse().status == 200
            assert time.monotonic() - start < 0.5
    finally:
        release.set()
