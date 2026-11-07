"""The clock stops when the game ends without a move (#287).

A resignation or an agreed draw left ``active`` set : the viewer and the
Tablier kept counting down the side to move after the end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard import chessboard_clock
from palamede_chessboard.chessboard_clock import ClockState, load_clock, save_clock
from palamede_chessboard.chessboard_state import GameState
from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.players import registry
from tests.mcp.conftest import _decode


class Clock:
    """A wall clock the test moves by hand."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.t = 1_000_000.0
        monkeypatch.setattr(chessboard_clock, "_now", lambda: self.t)

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def wall(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return Clock(monkeypatch)


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid, kind in (("stephane", "human"), ("bot-maia-1100", "agent")):
        registry.add_player(games_dir, pid, kind=kind)
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    return issued


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _game(client: TestClient, tokens: dict) -> str:
    r = client.post(
        "/api/games",
        json={"opponent": "bot-maia-1100", "color": "white", "time_control": {"format": "15+10"}},
        headers=_auth(tokens["stephane"]),
    )
    assert r.status_code == 201, r.text
    return r.json()["game_id"]


def _move(client: TestClient, game_id: str, token: str, uci: str) -> None:
    r = client.post(f"/api/games/{game_id}/move", json={"uci": uci}, headers=_auth(token))
    assert r.status_code == 200, r.text


def _read(client: TestClient, game_id: str) -> dict:
    return client.get(f"/api/clock?game_id={game_id}").json()["clock"]


def test_a_viewer_resignation_stops_the_clock(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    """The report of 07/11 : 1.e4 e5, then White resigns by the king."""
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    _move(client, game_id, tokens["bot-maia-1100"], "e7e5")
    before = load_clock(game_id, games_dir=games_dir)
    assert before is not None and before.active == "white"
    wall.advance(30)
    r = client.post(f"/api/games/{game_id}/resign", headers=_auth(tokens["stephane"]))
    assert r.status_code == 200
    after = load_clock(game_id, games_dir=games_dir)
    assert after is not None and after.active is None and not after.held
    # The 30 s thought before resigning are charged once, then nothing more.
    assert after.white_seconds == pytest.approx(before.white_seconds - 30)
    wall.advance(120)
    shown = _read(client, game_id)
    assert shown["active"] is None
    assert shown["white_seconds"] == pytest.approx(before.white_seconds - 30)


@pytest.mark.asyncio
async def test_mcp_resignation_and_agreed_draw_stop_the_clock(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock, monkeypatch
) -> None:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(games_dir))
    for end in ("resign", "draw"):
        game_id = _game(client, tokens)
        _move(client, game_id, tokens["stephane"], "e2e4")
        if end == "resign":
            out = _decode(await call_tool("chess_resign", {"game_id": game_id, "by": "black"}))
        else:
            await call_tool("chess_offer_draw", {"game_id": game_id, "by": "white"})
            out = _decode(await call_tool("chess_agree_draw", {"game_id": game_id, "by": "black"}))
        assert out["status"] == "ok", out
        clock = load_clock(game_id, games_dir=games_dir)
        assert clock is not None and clock.active is None, end


def test_a_game_over_with_a_running_clock_shows_it_stopped(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    """A game resigned before #287 : its clock still names a side, shown stopped."""
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    state = GameState.load(game_id, games_dir=games_dir)
    state.resign(by="white")  # the old path : the clock is left running
    clock = load_clock(game_id, games_dir=games_dir)
    assert clock is not None and clock.active == "black"
    wall.advance(60)
    shown = _read(client, game_id)
    assert shown["active"] is None
    assert shown["black_seconds"] == pytest.approx(clock.black_seconds)
    # Shown, not rewritten : the file keeps what it had.
    kept = load_clock(game_id, games_dir=games_dir)
    assert kept is not None and kept.active == "black"


def test_a_game_in_progress_keeps_its_clock_running(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    assert _read(client, game_id)["active"] == "black"


def test_stop_charges_nothing_during_a_hold() -> None:
    """A hold (#164) pushed last_move_ts ahead : no time is charged before it ends."""
    clock = ClockState("g", 300.0, 300.0, 0, active="white", last_move_ts=1_000.0, started=True)
    clock.held = True
    assert clock.stop(now=990.0)
    assert (clock.white_seconds, clock.active, clock.held) == (300.0, None, False)


def test_stop_of_a_clock_not_running_changes_nothing(games_dir: Path) -> None:
    clock = ClockState("g", 300.0, 300.0, 0)
    assert not clock.stop(now=5.0)
    save_clock(clock, games_dir=games_dir)
    assert load_clock("g", games_dir=games_dir) == clock


def test_stop_never_goes_below_zero() -> None:
    clock = ClockState("g", 10.0, 300.0, 0, active="white", last_move_ts=0.0, started=True)
    assert clock.stop(now=50.0)
    assert clock.white_seconds == 0.0
