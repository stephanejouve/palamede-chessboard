"""The reply waits for the clock (#316), on the MCP side : chess_play_move and chess_clock_start."""

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
async def test_the_reply_is_refused_until_the_clock_starts(games_dir: Path) -> None:
    await _create("mcp-reply")
    assert (await _play("mcp-reply", "e4"))["status"] == "ok"
    refused = await _play("mcp-reply", "e5")
    assert refused["status"] == "error" and "Pendule non démarrée" in str(refused)
    await call_tool("chess_clock_start", {"game_id": "mcp-reply"})
    assert (await _play("mcp-reply", "e5"))["status"] == "ok"


@pytest.mark.asyncio
async def test_started_after_the_first_move_black_runs(games_dir: Path) -> None:
    await _create("mcp-late")
    await _play("mcp-late", "e4")
    body = _decode(await call_tool("chess_clock_start", {"game_id": "mcp-late"}))
    assert body["clock"]["active"] == "black"


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


@pytest.mark.asyncio
async def test_a_tablier_game_is_not_started_by_hand(games_dir: Path) -> None:
    # A tablier.* id is reserved to the viewer (#131) : the game is made here.
    tc = TimeControl(format="15+10")
    meta = GameMetadata(game_id="tablier.mcp", white="w", black="b", time_control=tc)
    GameState.create(meta, games_dir=games_dir)
    init_and_save_clock("tablier.mcp", tc, games_dir=games_dir)
    body = _decode(await call_tool("chess_clock_start", {"game_id": "tablier.mcp"}))
    assert body["status"] == "error" and "Tablier" in str(body)
    assert (await _play("tablier.mcp", "e4"))["clock"]["started"] is True
