"""Follow-ups of #288 (#289) : the end and the clock under one lock, the
result read alone when the clock is read.

1. A resignation or an agreed draw, and the clock stopped after it, happen
   under the game's lock, the one a flag fall takes : the two ends cannot
   cross.
2. Showing a finished game's clock stopped reads the ``Result`` header
   only : the viewer asks for the clock every 5 s, every move need not be
   replayed for it.
"""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard import clock_moves
from palamede_chessboard._mcp_tool_handlers import _moves
from palamede_chessboard.chessboard_clock import load_clock
from palamede_chessboard.chessboard_state import GameState
from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.viewer.routers import games as games_router
from tests.mcp.conftest import _decode
from tests.viewer import test_clock_stops_at_the_end as stops

_auth, _game, _move, _read = stops._auth, stops._game, stops._move, stops._read
tokens, wall = stops.tokens, stops.wall  # the fixtures of #288


def _held(games_dir: Path, game_id: str) -> bool:
    """Whether someone holds the game's lock : a second ``flock`` would wait."""
    with (games_dir / f"{game_id}.pgn.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return False


@pytest.fixture
def seen(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """Each end and each clock stop, with whether the lock was held then."""
    calls: list[tuple[str, bool]] = []

    def spy(name: str, real: Any, game_id_of: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            calls.append((name, _held(games_dir, game_id_of(*args))))
            return real(*args, **kwargs)

        return wrapper

    for method in ("resign", "agree_draw"):
        real = getattr(GameState, method)
        monkeypatch.setattr(
            GameState, method, spy(method, real, lambda self, *a: self.metadata.game_id)
        )
    for module in (_moves, games_router):
        monkeypatch.setattr(
            module,
            "stop_clock_locked",
            spy("stop", clock_moves.stop_clock_locked, lambda game_id, *a: game_id),
        )
    return calls


def test_a_viewer_resignation_and_its_clock_stop_hold_the_lock(
    client: TestClient,
    tokens: dict,
    seen: list,
) -> None:
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    r = client.post(f"/api/games/{game_id}/resign", headers=_auth(tokens["stephane"]))
    assert r.status_code == 200, r.text
    assert seen == [("resign", True), ("stop", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["resign", "agree_draw"])
async def test_an_mcp_end_and_its_clock_stop_hold_the_lock(
    client: TestClient,
    tokens: dict,
    games_dir: Path,
    seen: list,
    monkeypatch: pytest.MonkeyPatch,
    end: str,
) -> None:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(games_dir))
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    if end == "agree_draw":
        await call_tool("chess_offer_draw", {"game_id": game_id, "by": "white"})
        out = _decode(await call_tool("chess_agree_draw", {"game_id": game_id, "by": "black"}))
    else:
        out = _decode(await call_tool("chess_resign", {"game_id": game_id, "by": "black"}))
    assert out["status"] == "ok", out
    assert seen == [(end, True), ("stop", True)]


def test_a_viewer_resignation_after_the_flag_fell_is_refused(
    client: TestClient,
    tokens: dict,
    games_dir: Path,
    wall,
) -> None:
    """The flag fell first : the game is lost on time, the resignation comes too late."""
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    wall.advance(16 * 60)
    r_clock = client.get(f"/api/clock?game_id={game_id}", headers=_auth(tokens["stephane"])).json()[
        "clock"
    ]
    assert r_clock["flag_fall"] == "black"
    r = client.post(f"/api/games/{game_id}/resign", headers=_auth(tokens["bot-maia-1100"]))
    assert r.status_code == 409
    state = GameState.load(game_id, games_dir=games_dir)
    assert (state.metadata.result, state.metadata.termination) == ("1-0", "time forfeit")


def test_a_resignation_on_a_missing_game_is_404(client: TestClient, tokens: dict) -> None:
    r = client.post("/api/games/tablier.nobody.x/resign", headers=_auth(tokens["stephane"]))
    assert r.status_code == 404


def test_the_clock_of_a_game_over_reads_the_result_header_only(
    client: TestClient,
    tokens: dict,
    games_dir: Path,
    wall,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    GameState.load(game_id, games_dir=games_dir).resign(by="white")  # clock left running

    def replay(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("every move replayed to read a clock")

    monkeypatch.setattr(GameState, "load", replay)
    assert _read(client, game_id)["active"] is None


def test_the_clock_of_a_game_in_progress_replays_no_move(
    client: TestClient,
    tokens: dict,
    games_dir: Path,
    wall,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    monkeypatch.setattr(GameState, "load", lambda *a, **k: pytest.fail("PGN replayed"))
    assert _read(client, game_id)["active"] == "black"
    clock = load_clock(game_id, games_dir=games_dir)
    assert clock is not None and clock.active == "black"


def test_a_viewer_move_after_a_resignation_is_refused(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    """Legal on the board, but the game is over : 409, nothing changes (#299)."""
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    assert client.post(f"/api/games/{game_id}/resign", headers=_auth(tokens["stephane"])).is_success
    r = client.post(
        f"/api/games/{game_id}/move",
        json={"uci": "e7e5"},
        headers=_auth(tokens["bot-maia-1100"]),
    )
    assert r.status_code == 409, r.text
    state = GameState.load(game_id, games_dir=games_dir)
    assert state.metadata.result == "0-1"
    assert state.board.ply() == 1


def test_a_viewer_move_after_the_flag_fell_is_refused(
    client: TestClient, tokens: dict, games_dir: Path, wall
) -> None:
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    wall.advance(16 * 60)
    clock = client.get(f"/api/clock?game_id={game_id}", headers=_auth(tokens["stephane"])).json()
    assert clock["clock"]["flag_fall"] == "black"
    r = client.post(
        f"/api/games/{game_id}/move",
        json={"uci": "e7e5"},
        headers=_auth(tokens["bot-maia-1100"]),
    )
    assert r.status_code == 409, r.text
    state = GameState.load(game_id, games_dir=games_dir)
    assert state.metadata.result == "1-0"
    assert state.metadata.termination == "time forfeit"
    assert state.board.ply() == 1


@pytest.mark.asyncio
async def test_an_mcp_move_after_a_resignation_is_invalid_state(
    client: TestClient,
    tokens: dict,
    games_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(games_dir))
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    await call_tool("chess_resign", {"game_id": game_id, "by": "white"})
    out = _decode(await call_tool("chess_play_move", {"game_id": game_id, "move_san": "e5"}))
    assert (out["status"], out["code"]) == ("error", "INVALID_STATE")
    assert GameState.load(game_id, games_dir=games_dir).board.ply() == 1
