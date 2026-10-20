"""Tests for the ``rated`` parameter of ``chess_create_game`` (issue #33).

Exercises and friendly games are created with ``rated=false`` : no
``[Rated]`` header is written, so they stay out of the Elo
(``collect_rated_games``, ``chess_get_elo``) and out of the matchmaking
busy check. The default stays ``true``, and only a strict JSON boolean
is accepted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.chess_elo import collect_rated_games
from palamede_chessboard.chess_matchmaking import _scan_active_games
from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode

#: Endgame study position (white to move), as an exercise would use.
EXERCISE_FEN = "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"


async def _create(game_id: str, **extra) -> dict:
    args = {"game_id": game_id, "white": "stephane", "black": "claude-desktop", **extra}
    return _decode(await call_tool("chess_create_game", args))


def _pgn(games_dir: Path, game_id: str) -> str:
    return (games_dir / f"{game_id}.pgn").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_default_is_rated(games_dir: Path) -> None:
    body = await _create("default-game")
    assert body["status"] == "ok"
    assert '[Rated "true"]' in _pgn(games_dir, "default-game")


@pytest.mark.asyncio
async def test_rated_true_explicit(games_dir: Path) -> None:
    body = await _create("rated-game", rated=True)
    assert body["status"] == "ok"
    assert '[Rated "true"]' in _pgn(games_dir, "rated-game")


@pytest.mark.asyncio
async def test_rated_false_writes_no_header(games_dir: Path) -> None:
    body = await _create("exo-001", rated=False, starting_fen=EXERCISE_FEN)
    assert body["status"] == "ok"
    assert body["state"]["metadata"]["rated"] is False
    assert "[Rated " not in _pgn(games_dir, "exo-001")


@pytest.mark.parametrize("bad", ["false", "true", 0, 1, None, [], {}])
@pytest.mark.asyncio
async def test_non_boolean_rated_is_refused(games_dir: Path, bad) -> None:
    body = await _create("bad-rated", rated=bad)
    assert body["status"] == "error"
    assert body["code"] == "CHESSBOARD_ERROR"
    assert "rated must be a JSON boolean" in body["reason"]
    assert not (games_dir / "bad-rated.pgn").exists()


@pytest.mark.asyncio
async def test_finished_unrated_game_stays_out_of_elo(games_dir: Path) -> None:
    """End-to-end : an unrated game played to the end never reaches the Elo."""
    await _create("exo-002", rated=False)
    await _create("rated-002")
    await call_tool("chess_resign", {"game_id": "exo-002", "by": "black"})
    await call_tool("chess_resign", {"game_id": "rated-002", "by": "black"})

    ids = {rg.game_id for rg in collect_rated_games(games_dir=games_dir)}
    assert ids == {"rated-002"}

    elo = _decode(await call_tool("chess_get_elo", {}))
    assert elo["status"] == "ok"
    # Only rated-002 counts : both players exist once, with a single game each.
    games_by_name = {p["name"]: p["games"] for p in elo["players"]}
    assert all(g == 1 for g in games_by_name.values())


@pytest.mark.asyncio
async def test_unrated_game_in_progress_does_not_make_player_busy(games_dir: Path) -> None:
    await _create("exo-003", rated=False)
    await _create("rated-003", white="junior", black="leader")

    busy = _scan_active_games(games_dir)
    assert busy == [{"white": "junior", "black": "leader"}]
