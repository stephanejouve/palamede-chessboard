"""The clock on every path a move takes (#135).

A move from the viewer (Chessnut board, mouse, Maia) advances the clock
like ``chess_play_move`` ; the clock starts on the first move ; a flag
that fell ends the game when the clock is read, without a move.
"""

from __future__ import annotations

from pathlib import Path

import chess
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


def _read(client: TestClient, game_id: str, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(f"/api/clock?game_id={game_id}", headers=headers).json()["clock"]


def test_flag_fall_at_reading_time_without_a_move(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    """A player who stops playing loses on time when a player reads the clock."""
    game_id = _game(client, tokens, fmt="1+0")
    _move(client, game_id, tokens["stephane"], "e2e4")
    wall.advance(30)
    assert _read(client, game_id, tokens["stephane"])["flag_fall"] is None
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "*"
    wall.advance(31)
    body = _read(client, game_id, tokens["stephane"])
    assert body["flag_fall"] == "black" and body["black_seconds"] == 0
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "1-0"
    # Read again : nothing more happens.
    assert _read(client, game_id)["flag_fall"] == "black"


@pytest.mark.parametrize("who", [None, "pas-un-jeton", "patrice"])
def test_only_a_player_or_the_arbiter_ends_the_game_on_time(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock, who
) -> None:
    """Owner, 31/10 (FIDE 6.8) : a spectator's read, with or without a token,
    shows the clock and ends nothing ; the next player's read does."""
    registry.add_player(games_dir, "patrice", kind="human")
    tokens["patrice"] = registry.issue_token(games_dir, "patrice", label="tests")
    game_id = _game(client, tokens, fmt="1+0")
    _move(client, game_id, tokens["stephane"], "e2e4")
    wall.advance(61)
    assert _read(client, game_id, tokens.get(who, who))["flag_fall"] is None
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "*"
    assert _read(client, game_id, tokens["bot-maia-1100"])["flag_fall"] == "black"
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "1-0"


def _tablier_game(games_dir: Path, fen: str, arbiter: str | None = None) -> str:
    from palamede_chessboard.chessboard_clock import init_and_save_clock

    game_id = "tablier.stephane.20261031-010000-6d9a"
    GameState.create(
        GameMetadata(
            game_id=game_id,
            white="stephane",
            black="bot-maia-1100",
            arbiter=arbiter,
            time_control=TimeControl(format="1+0"),
        ),
        games_dir=games_dir,
        starting_fen=fen,
    )
    init_and_save_clock(game_id, TimeControl(format="1+0"), games_dir=games_dir)
    return game_id


def test_the_arbiter_ends_the_game_on_time(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    registry.add_player(games_dir, "leader-bot", kind="agent")
    arbiter = registry.issue_token(games_dir, "leader-bot", label="tests")
    game_id = _tablier_game(games_dir, chess.STARTING_FEN, arbiter="leader-bot")
    _move(client, game_id, tokens["stephane"], "e2e4")
    wall.advance(61)
    assert _read(client, game_id, arbiter)["flag_fall"] == "black"
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "1-0"


#: White, king and queen ; Black, king and bishop : Black can no longer mate.
QUEEN_AGAINST_BISHOP = "2b1k3/8/8/8/8/8/8/3QK3 w - - 0 1"


def test_out_of_time_against_no_mating_material_is_a_draw(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    """FIDE 6.9 : White runs out, but a lone king and bishop cannot mate."""
    game_id = _tablier_game(games_dir, QUEEN_AGAINST_BISHOP)
    _move(client, game_id, tokens["stephane"], "d1d2")  # starts the clock
    _move(client, game_id, tokens["bot-maia-1100"], "c8d7")
    wall.advance(61)
    assert _read(client, game_id, tokens["stephane"])["flag_fall"] == "white"
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "1/2-1/2"


def test_out_of_time_against_mating_material_is_lost(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    """FIDE 6.9, the other way : Black runs out against a queen, Black loses."""
    game_id = _tablier_game(games_dir, QUEEN_AGAINST_BISHOP)
    _move(client, game_id, tokens["stephane"], "d1d2")
    wall.advance(61)
    assert _read(client, game_id, tokens["stephane"])["flag_fall"] == "black"
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "1-0"


def test_flag_on_a_move_against_no_mating_material_is_a_draw(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    """FIDE 6.9 on the move path too : the flag found when White plays late."""
    game_id = _tablier_game(games_dir, QUEEN_AGAINST_BISHOP)
    _move(client, game_id, tokens["stephane"], "d1d2")
    _move(client, game_id, tokens["bot-maia-1100"], "c8d7")
    wall.advance(61)
    _move(client, game_id, tokens["stephane"], "d2d7")  # takes the bishop, too late
    assert load_clock(game_id, games_dir=games_dir).flag_fall == "white"
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "1/2-1/2"


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


def _agents_game(games_dir: Path, game_id: str, fmt: str) -> None:
    from palamede_chessboard.chessboard_clock import init_and_save_clock

    GameState.create(
        GameMetadata(game_id=game_id, white="w", black="b", time_control=TimeControl(format=fmt)),
        games_dir=games_dir,
    )
    init_and_save_clock(game_id, TimeControl(format=fmt), games_dir=games_dir)


@pytest.mark.asyncio
async def test_agents_game_waits_for_the_arbiter(games_dir: Path, wall: Clock) -> None:
    """Owner 29/10, review Leader #136 : the start on the first move is for Tablier only."""
    _agents_game(games_dir, "mcp-clk", "5+3")
    wall.advance(42)
    await call_tool("chess_play_move", {"game_id": "mcp-clk", "move_san": "e4", "by": "w"})
    assert load_clock("mcp-clk", games_dir=games_dir).started is False


@pytest.mark.asyncio
async def test_mcp_path_uses_the_same_helper_once_started(games_dir: Path, wall: Clock) -> None:
    _agents_game(games_dir, "mcp-run", "5+3")
    assert _decode(await call_tool("chess_clock_start", {"game_id": "mcp-run"}))["status"] == "ok"
    wall.advance(10)
    await call_tool("chess_play_move", {"game_id": "mcp-run", "move_san": "e4", "by": "w"})
    wall.advance(20)
    await call_tool("chess_play_move", {"game_id": "mcp-run", "move_san": "e5", "by": "b"})
    clock = load_clock("mcp-run", games_dir=games_dir)
    assert clock.white_seconds == 300 - 10 + 3
    assert clock.black_seconds == 300 - 20 + 3


@pytest.mark.asyncio
async def test_reading_never_ends_an_agents_game_on_time(games_dir: Path, wall: Clock) -> None:
    """A spectator's read of /api/clock or chess_clock_state never decides a rated game."""
    _agents_game(games_dir, "mcp-flag", "1+0")
    await call_tool("chess_clock_start", {"game_id": "mcp-flag"})
    wall.advance(61)
    body = _decode(await call_tool("chess_clock_state", {"game_id": "mcp-flag"}))
    assert body["clock"]["flag_fall"] is None
    assert GameState.load("mcp-flag", games_dir=games_dir).metadata.result == "*"


@pytest.mark.asyncio
async def test_mcp_clock_state_settles_a_tablier_flag(
    client: TestClient,
    tokens: dict,
    games_dir: Path,
    wall: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over MCP too, only a player's (or the arbiter's) token ends the game."""
    from palamede_chessboard import mcp_chessboard_server

    game_id = _game(client, tokens, fmt="1+0")
    _move(client, game_id, tokens["stephane"], "e2e4")
    wall.advance(61)
    body = _decode(await call_tool("chess_clock_state", {"game_id": game_id}))  # stdio
    assert body["clock"]["flag_fall"] is None
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "*"
    headers = {"authorization": f"Bearer {tokens['stephane']}"}
    monkeypatch.setattr(mcp_chessboard_server, "_request_headers", lambda: headers)
    body = _decode(await call_tool("chess_clock_state", {"game_id": game_id}))
    assert body["clock"]["flag_fall"] == "black"
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "1-0"


def test_a_move_and_a_read_take_the_same_lock(
    client: TestClient, tokens: dict, games_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Leader #136 : the move and settle_flag hold one lock per game."""
    from palamede_chessboard import clock_moves

    taken = []
    real = clock_moves._atomic.locked

    def spy(path):
        taken.append(path.name)
        return real(path)

    game_id = _game(client, tokens)
    monkeypatch.setattr(clock_moves._atomic, "locked", spy)
    _move(client, game_id, tokens["stephane"], "e2e4")
    client.get(f"/api/clock?game_id={game_id}")
    assert taken == [f"{game_id}.pgn", f"{game_id}.pgn"]


# Review Leader #136 : the lock is never taken for a made-up or malformed id.


def _locks(games_dir: Path) -> list[str]:
    return sorted(p.name for p in games_dir.rglob("*.lock"))


@pytest.mark.parametrize("game_id", ["tablier.stephane.20991231-000000-dead", "tablier.x", "ghost"])
def test_reading_a_missing_game_creates_nothing(
    client: TestClient, tokens: dict, games_dir: Path, game_id: str
) -> None:
    before = _locks(games_dir)
    r = client.get(f"/api/clock?game_id={game_id}")
    assert r.status_code == 200 and r.json()["clock"] is None
    assert _locks(games_dir) == before


@pytest.mark.parametrize("token", [None, "stephane"], ids=["sans jeton", "avec jeton"])
def test_moving_in_a_missing_game_is_404_and_creates_nothing(
    client: TestClient, tokens: dict, games_dir: Path, token
) -> None:
    before = _locks(games_dir)
    headers = {"Authorization": f"Bearer {tokens[token]}"} if token else {}
    r = client.post(
        "/api/games/tablier.stephane.20991231-000000-dead/move",
        json={"uci": "e2e4"},
        headers=headers,
    )
    assert r.status_code == 404
    assert _locks(games_dir) == before


@pytest.mark.parametrize("game_id", ["tablier./../x", "../x", "a b"])
def test_malformed_id_is_400_not_500(
    client: TestClient, tokens: dict, games_dir: Path, game_id: str
) -> None:
    before = _locks(games_dir)
    assert client.get("/api/clock", params={"game_id": game_id}).status_code == 400
    assert _locks(games_dir) == before
