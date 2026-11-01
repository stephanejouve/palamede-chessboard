"""The relay hold (#164) : a side played on an eBoard does not pay for setting
the opponent's move ; its time runs once its board matches, 20 s at most."""

from __future__ import annotations

import json
from pathlib import Path

import chess
import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_clock import HOLD_SECONDS, ClockState, load_clock
from palamede_chessboard.players import registry
from tests.viewer.test_clock_every_move import Clock, _game, _move, _read


@pytest.fixture
def wall(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return Clock(monkeypatch)


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Stéphane, human, and the bot Maia, as in ``test_clock_every_move``."""
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid, kind in (("stephane", "human"), ("bot-maia-1100", "agent")):
        registry.add_player(games_dir, pid, kind=kind)
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    return issued


def _post(client: TestClient, game_id: str, route: str, token: str | None, body: dict):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(f"/api/games/{game_id}/{route}", json=body, headers=headers)


def _board_fen(client: TestClient, game_id: str, games_dir: Path) -> str:
    from palamede_chessboard.chessboard_state import GameState

    return GameState.load(game_id, games_dir=games_dir).board.board_fen()


def _held_game(client: TestClient, tokens: dict, wall: Clock, fmt: str = "5+3") -> str:
    """Stéphane (White) on the board ; Maia has just answered 1.e4 with 1...e5 in 10 s."""
    game_id = _game(client, tokens, fmt)
    r = _post(client, game_id, "board", tokens["stephane"], {"board": True})
    assert r.status_code == 200 and r.json()["colors"] == ["white"]
    assert _move(client, game_id, tokens["stephane"], "e2e4").status_code == 200
    wall.advance(10)
    assert _move(client, game_id, tokens["bot-maia-1100"], "e7e5").status_code == 200
    return game_id


def test_the_board_side_is_held_until_its_board_matches(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _held_game(client, tokens, wall)
    clock = load_clock(game_id, games_dir=games_dir)
    assert clock.held and clock.active == "white" and clock.board_sides == ["white"]
    assert clock.black_seconds == 303 - 10 + 3  # Maia, without a board : not held
    wall.advance(15)  # setting 1...e5 on the board
    assert _read(client, game_id, tokens["stephane"])["flag_fall"] is None
    stale = chess.Board().board_fen()
    r = _post(client, game_id, "synced", tokens["stephane"], {"fen": stale})
    assert r.json()["released"] is False  # an older position releases nothing
    fen = _board_fen(client, game_id, games_dir)
    assert _post(client, game_id, "synced", tokens["stephane"], {"fen": fen}).json() == {
        "status": "ok",
        "released": True,
    }
    wall.advance(5)  # thinking
    assert _move(client, game_id, tokens["stephane"], "g1f3").status_code == 200
    assert load_clock(game_id, games_dir=games_dir).white_seconds == 303 - 5 + 3


def test_the_hold_ends_after_its_ceiling(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    """A silent bridge, or a board never set : the time runs after 20 s anyway."""
    game_id = _held_game(client, tokens, wall)
    wall.advance(HOLD_SECONDS + 10)
    assert _move(client, game_id, tokens["stephane"], "g1f3").status_code == 200
    assert load_clock(game_id, games_dir=games_dir).white_seconds == 303 - 10 + 3


def test_no_flag_falls_during_the_hold(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _held_game(client, tokens, wall, fmt="1+0")
    wall.advance(HOLD_SECONDS + 59)
    assert _read(client, game_id, tokens["stephane"])["flag_fall"] is None
    wall.advance(2)
    assert _read(client, game_id, tokens["stephane"])["flag_fall"] == "white"


def test_only_the_side_to_move_releases_its_hold(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _held_game(client, tokens, wall)
    fen = _board_fen(client, game_id, games_dir)
    assert (
        _post(client, game_id, "synced", tokens["bot-maia-1100"], {"fen": fen}).status_code == 403
    )
    assert _post(client, game_id, "synced", None, {"fen": fen}).status_code == 401
    assert load_clock(game_id, games_dir=games_dir).held


def test_leaving_the_board_releases_at_once(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    """The « Échiquier » switch to the mouse (#152) : nothing left to set, the time runs."""
    game_id = _held_game(client, tokens, wall)
    wall.advance(5)
    r = _post(client, game_id, "board", tokens["stephane"], {"board": False})
    assert r.status_code == 200
    clock = load_clock(game_id, games_dir=games_dir)
    assert not clock.held and clock.board_sides == []
    wall.advance(4)
    assert _move(client, game_id, tokens["stephane"], "g1f3").status_code == 200
    assert load_clock(game_id, games_dir=games_dir).white_seconds == 303 - 4 + 3


def test_a_side_without_a_board_is_never_held(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _game(client, tokens)
    _move(client, game_id, tokens["stephane"], "e2e4")
    _move(client, game_id, tokens["bot-maia-1100"], "e7e5")
    assert not load_clock(game_id, games_dir=games_dir).held


def test_the_board_needs_a_player_of_the_game(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    game_id = _game(client, tokens)
    assert _post(client, game_id, "board", None, {"board": True}).status_code == 401
    assert _post(client, game_id, "board", "pas-un-jeton", {"board": True}).status_code == 401
    assert _post(client, game_id, "board", tokens["stephane"], {"board": "yes"}).status_code == 422
    assert load_clock(game_id, games_dir=games_dir).board_sides == []


def test_a_clock_saved_before_the_hold_still_loads(games_dir: Path) -> None:
    saved = {
        "game_id": "old",
        "white_seconds": 300.0,
        "black_seconds": 300.0,
        "increment_seconds": 3,
        "active": "white",
        "last_move_ts": 1.0,
        "started": True,
        "flag_fall": None,
    }
    (games_dir / "clock_old.json").write_text(json.dumps(saved))
    clock = load_clock("old", games_dir=games_dir)
    assert isinstance(clock, ClockState) and clock.board_sides == [] and not clock.held
