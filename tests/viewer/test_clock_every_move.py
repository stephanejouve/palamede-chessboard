"""The clock on every path a move takes (#135).

A move from the viewer (Chessnut board, mouse, Maia) advances the clock
like ``chess_play_move`` ; the clock starts on the first move ; a flag
that fell ends the game when the clock is read, without a move.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard import chessboard_clock
from palamede_chessboard.chessboard_clock import load_clock
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
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


def _game(client: TestClient, tokens: dict, fmt: str = "5+3") -> str:
    r = client.post(
        "/api/games",
        json={"opponent": "bot-maia-1100", "color": "white", "time_control": {"format": fmt}},
        headers={"Authorization": f"Bearer {tokens['stephane']}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["game_id"]


def _move(client: TestClient, game_id: str, token: str, uci: str):
    return client.post(
        f"/api/games/{game_id}/move",
        json={"uci": uci},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_first_white_move_starts_the_clock_for_black(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _game(client, tokens)
    assert load_clock(game_id, games_dir=games_dir).started is False
    wall.advance(42)  # White thinks before the first move : it costs nothing
    r = _move(client, game_id, tokens["stephane"], "e2e4")
    assert r.status_code == 200
    clock = load_clock(game_id, games_dir=games_dir)
    assert clock.started and clock.active == "black"
    assert clock.white_seconds == 300
    assert r.json()["clock"]["active"] == "black"


def test_viewer_moves_spend_time_and_add_the_increment(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    """Maia plays Black through the viewer route, like the bot in production."""
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    wall.advance(10)
    assert _move(client, game_id, tokens["bot-maia-1100"], "e7e5").status_code == 200
    wall.advance(20)
    assert _move(client, game_id, tokens["stephane"], "g1f3").status_code == 200
    clock = load_clock(game_id, games_dir=games_dir)
    assert clock.black_seconds == 300 - 10 + 3
    assert clock.white_seconds == 300 - 20 + 3
    assert clock.active == "black"


def test_refused_move_leaves_the_clock_alone(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    before = load_clock(game_id, games_dir=games_dir)
    wall.advance(30)
    assert _move(client, game_id, tokens["stephane"], "d2d4").status_code == 403  # not his turn
    assert _move(client, game_id, tokens["bot-maia-1100"], "e7e1").status_code == 422  # illegal
    assert _move(client, game_id, "pas-un-jeton", "e7e5").status_code == 401
    assert load_clock(game_id, games_dir=games_dir) == before


def test_flag_fall_on_a_viewer_move_ends_the_game(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _game(client, tokens, fmt="1+0")
    _move(client, game_id, tokens["stephane"], "e2e4")
    wall.advance(61)
    _move(client, game_id, tokens["bot-maia-1100"], "e7e5")
    assert load_clock(game_id, games_dir=games_dir).flag_fall == "black"
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "1-0"


def test_flag_fall_at_reading_time_without_a_move(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    """A player who stops playing loses on time when the clock is read."""
    game_id = _game(client, tokens, fmt="1+0")
    _move(client, game_id, tokens["stephane"], "e2e4")
    wall.advance(30)
    assert client.get(f"/api/clock?game_id={game_id}").json()["clock"]["flag_fall"] is None
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "*"
    wall.advance(31)
    body = client.get(f"/api/clock?game_id={game_id}").json()["clock"]
    assert body["flag_fall"] == "black" and body["black_seconds"] == 0
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "1-0"
    # Read again : nothing more happens.
    assert client.get(f"/api/clock?game_id={game_id}").json()["clock"]["flag_fall"] == "black"


def test_no_flag_at_reading_time_once_the_game_is_over(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _game(client, tokens, fmt="1+0")
    _move(client, game_id, tokens["stephane"], "e2e4")
    assert (
        client.post(
            f"/api/games/{game_id}/resign",
            headers={"Authorization": f"Bearer {tokens['stephane']}"},
        ).status_code
        == 200
    )
    wall.advance(3600)
    assert client.get(f"/api/clock?game_id={game_id}").json()["clock"]["flag_fall"] is None
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "0-1"


def test_clock_stops_at_mate(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _game(client, tokens)
    for uci, who in (
        ("f2f3", "stephane"),
        ("e7e5", "bot-maia-1100"),
        ("g2g4", "stephane"),
        ("d8h4", "bot-maia-1100"),
    ):
        assert _move(client, game_id, tokens[who], uci).status_code == 200
    assert load_clock(game_id, games_dir=games_dir).active is None


def test_game_without_time_control_has_no_clock(client: TestClient, tokens: dict) -> None:
    game_id = _game(client, tokens, fmt="unrated")
    r = _move(client, game_id, tokens["stephane"], "e2e4")
    assert r.json()["clock"] is None
    assert client.get(f"/api/clock?game_id={game_id}").json()["clock"] is None


@pytest.mark.asyncio
async def test_mcp_path_uses_the_same_rules(games_dir: Path, wall: Clock) -> None:
    """chess_play_move : start on the first move, then Fischer, as the viewer."""
    GameState.create(
        GameMetadata(
            game_id="mcp-clk", white="w", black="b", time_control=TimeControl(format="5+3")
        ),
        games_dir=games_dir,
    )
    from palamede_chessboard.chessboard_clock import init_and_save_clock

    init_and_save_clock("mcp-clk", TimeControl(format="5+3"), games_dir=games_dir)
    wall.advance(42)
    assert (
        _decode(
            await call_tool("chess_play_move", {"game_id": "mcp-clk", "move_san": "e4", "by": "w"})
        )["status"]
        == "ok"
    )
    clock = load_clock("mcp-clk", games_dir=games_dir)
    assert clock.started and clock.active == "black" and clock.white_seconds == 300
    wall.advance(10)
    await call_tool("chess_play_move", {"game_id": "mcp-clk", "move_san": "e5", "by": "b"})
    assert load_clock("mcp-clk", games_dir=games_dir).black_seconds == 300 - 10 + 3


@pytest.mark.asyncio
async def test_mcp_clock_state_settles_the_flag(games_dir: Path, wall: Clock) -> None:
    GameState.create(
        GameMetadata(
            game_id="mcp-flag", white="w", black="b", time_control=TimeControl(format="1+0")
        ),
        games_dir=games_dir,
    )
    from palamede_chessboard.chessboard_clock import init_and_save_clock

    init_and_save_clock("mcp-flag", TimeControl(format="1+0"), games_dir=games_dir)
    await call_tool("chess_play_move", {"game_id": "mcp-flag", "move_san": "e4", "by": "w"})
    wall.advance(61)
    body = _decode(await call_tool("chess_clock_state", {"game_id": "mcp-flag"}))
    assert body["clock"]["flag_fall"] == "black"
    assert GameState.load("mcp-flag", games_dir=games_dir).metadata.result == "1-0"
