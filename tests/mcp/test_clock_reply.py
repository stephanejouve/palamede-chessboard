"""No move before the clock (FIDE 6.6 and 6.2.1, #330), on the MCP side : chess_play_move and chess_clock_start."""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard import chessboard_clock, clock_moves
from palamede_chessboard.chessboard_clock import init_and_save_clock
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode


async def _create(game_id: str) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": game_id, "white": "w", "black": "b", "time_control": {"format": "15+10"}},
    )


async def _play(game_id: str, san: str) -> dict:
    return _decode(await call_tool("chess_play_move", {"game_id": game_id, "move_san": san}))


@pytest.mark.asyncio
async def test_no_move_before_the_clock_the_first_one_included(games_dir: Path) -> None:
    await _create("mcp-fide")
    refused = await _play("mcp-fide", "e4")
    assert refused["status"] == "error"
    assert clock_moves.CLOCK_NOT_STARTED in refused["reason"]
    assert GameState.load("mcp-fide", games_dir=games_dir).san_history == []
    await call_tool("chess_clock_start", {"game_id": "mcp-fide"})
    assert (await _play("mcp-fide", "e4"))["status"] == "ok"
    assert (await _play("mcp-fide", "e5"))["status"] == "ok"


@pytest.mark.asyncio
async def test_the_start_runs_white(games_dir: Path) -> None:
    await _create("mcp-white")
    body = _decode(await call_tool("chess_clock_start", {"game_id": "mcp-white"}))
    assert (body["clock"]["started"], body["clock"]["active"]) == (True, "white")


@pytest.mark.asyncio
async def test_a_game_without_clock_plays_at_once(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "mcp-free", "white": "w", "black": "b", "time_control": {"format": "unrated"}},
    )
    assert (await _play("mcp-free", "e4"))["status"] == "ok"


@pytest.mark.asyncio
async def test_a_start_in_the_middle_of_the_game_changes_nothing(
    games_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chessboard_clock, "_now", lambda: 1000.0)
    await _create("mcp-mid")
    await call_tool("chess_clock_start", {"game_id": "mcp-mid"})
    await _play("mcp-mid", "e4")
    monkeypatch.setattr(chessboard_clock, "_now", lambda: 1500.0)
    body = _decode(await call_tool("chess_clock_start", {"game_id": "mcp-mid"}))
    assert (body["clock"]["active"], body["clock"]["last_move_ts"]) == ("black", 1000.0)


@pytest.mark.asyncio
async def test_the_start_takes_the_game_lock(
    games_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locked: list[str] = []
    real = clock_moves.game_lock
    monkeypatch.setattr(
        clock_moves, "game_lock", lambda game, gdir: (locked.append(game), real(game, gdir))[1]
    )
    await _create("mcp-lock")
    await call_tool("chess_clock_start", {"game_id": "mcp-lock"})
    assert locked == ["mcp-lock"]


@pytest.mark.asyncio
async def test_a_finished_game_cannot_start_its_clock(games_dir: Path) -> None:
    await _create("mcp-over")
    await call_tool("chess_resign", {"game_id": "mcp-over", "by": "white"})
    body = _decode(await call_tool("chess_clock_start", {"game_id": "mcp-over"}))
    assert body["status"] == "error"


def _tablier_game(games_dir: Path, game_id: str) -> None:
    # A tablier.* id is reserved to the viewer (#131) : the game is made here.
    tc = TimeControl(format="15+10")
    meta = GameMetadata(game_id=game_id, white="w", black="b", time_control=tc)
    GameState.create(meta, games_dir=games_dir)
    init_and_save_clock(game_id, tc, games_dir=games_dir)


@pytest.mark.asyncio
async def test_a_tablier_game_still_starts_on_its_first_move(games_dir: Path) -> None:
    """Transition until the Tablier has its own way to start it (#335)."""
    _tablier_game(games_dir, "tablier.mcp")
    assert (await _play("tablier.mcp", "e4"))["clock"]["started"] is True


@pytest.mark.asyncio
async def test_a_tablier_game_can_be_started_by_hand(games_dir: Path) -> None:
    """From chess_clock_start too (#330) : the same action as the viewer's key."""
    _tablier_game(games_dir, "tablier.mcp2")
    body = _decode(await call_tool("chess_clock_start", {"game_id": "tablier.mcp2"}))
    assert (body["status"], body["clock"]["active"]) == ("ok", "white")
