"""#164a : a declaration left by a dead bridge expires. A side that moves while
still held loses the hold, until its bridge says again that its board matches."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_clock import HOLD_SECONDS, load_clock
from tests.viewer.test_clock_every_move import Clock, _move
from tests.viewer.test_clock_hold import _board_fen, _held_game, _post, tokens, wall

__all__ = ["tokens", "wall"]  # the fixtures, used by name


def _silent_move(client: TestClient, tokens: dict, wall: Clock, games_dir: Path) -> str:
    """Stéphane's bridge never says its board matches : he moves while still held."""
    game_id = _held_game(client, tokens, wall)
    wall.advance(HOLD_SECONDS + 10)
    assert _move(client, game_id, tokens["stephane"], "g1f3").status_code == 200
    return game_id


def test_a_side_that_moves_while_held_loses_the_hold(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _silent_move(client, tokens, wall, games_dir)
    clock = load_clock(game_id, games_dir=games_dir)
    assert (clock.board_sides, clock.stale_sides) == ([], ["white"])
    assert clock.white_seconds == 303 - 10 + 3  # the time of that move : unchanged
    wall.advance(4)
    assert _move(client, game_id, tokens["bot-maia-1100"], "b8c6").status_code == 200
    assert not load_clock(game_id, games_dir=games_dir).held  # no longer held
    wall.advance(7)
    assert _move(client, game_id, tokens["stephane"], "f1b5").status_code == 200
    assert load_clock(game_id, games_dir=games_dir).white_seconds == 296 - 7 + 3


def test_a_released_hold_keeps_the_board(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _held_game(client, tokens, wall)
    fen = _board_fen(client, game_id, games_dir)
    assert _post(client, game_id, "synced", tokens["stephane"], {"fen": fen}).json()["released"]
    assert _move(client, game_id, tokens["stephane"], "g1f3").status_code == 200
    clock = load_clock(game_id, games_dir=games_dir)
    assert (clock.board_sides, clock.stale_sides) == (["white"], [])


def test_the_bridge_speaking_again_brings_the_hold_back(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _silent_move(client, tokens, wall, games_dir)
    assert _move(client, game_id, tokens["bot-maia-1100"], "b8c6").status_code == 200
    fen = _board_fen(client, game_id, games_dir)
    r = _post(client, game_id, "synced", tokens["stephane"], {"fen": fen})
    assert r.json()["released"] is False  # nothing was held on this move
    clock = load_clock(game_id, games_dir=games_dir)
    assert (clock.board_sides, clock.stale_sides) == (["white"], [])
    assert _move(client, game_id, tokens["stephane"], "f1b5").status_code == 200
    assert _move(client, game_id, tokens["bot-maia-1100"], "a7a6").status_code == 200
    assert load_clock(game_id, games_dir=games_dir).held  # held again


def test_an_older_position_brings_nothing_back(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _silent_move(client, tokens, wall, games_dir)
    stale_fen = _board_fen(client, game_id, games_dir)
    assert _move(client, game_id, tokens["bot-maia-1100"], "b8c6").status_code == 200
    _post(client, game_id, "synced", tokens["stephane"], {"fen": stale_fen})
    assert load_clock(game_id, games_dir=games_dir).stale_sides == ["white"]


def test_declaring_again_clears_the_stale_side(
    client: TestClient, tokens: dict, games_dir: Path, wall: Clock
) -> None:
    game_id = _silent_move(client, tokens, wall, games_dir)
    assert _post(client, game_id, "board", tokens["stephane"], {"board": False}).status_code == 200
    clock = load_clock(game_id, games_dir=games_dir)
    assert (clock.board_sides, clock.stale_sides) == ([], [])
    assert _post(client, game_id, "board", tokens["stephane"], {"board": True}).status_code == 200
    clock = load_clock(game_id, games_dir=games_dir)
    assert (clock.board_sides, clock.stale_sides) == (["white"], [])


def test_a_clock_with_an_unknown_field_still_loads(games_dir: Path) -> None:
    """A rollback reads a clock written by a newer version (#164a)."""
    games_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "game_id": "g",
        "white_seconds": 60.0,
        "black_seconds": 60.0,
        "increment_seconds": 0,
        "some_future_field": [1],
    }
    (games_dir / "clock_g.json").write_text(json.dumps(snapshot), encoding="utf-8")
    clock = load_clock("g", games_dir=games_dir)
    assert clock is not None and clock.stale_sides == [] and clock.white_seconds == 60.0
