"""``max_concurrent`` ceiling in the matchmaking busy check (issue #38)."""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.chess_matchmaking import (
    _available_players,
    _scan_active_games,
    schedule_round_from_disk,
)
from palamede_chessboard.player_prefs import PREFS_ENV, set_max_concurrent
from tests.chess_matchmaking.conftest import _write_pgn


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PREFS_ENV, raising=False)


def _live(gid: str, white: str, black: str) -> dict:
    return {"game_id": gid, "white": white, "black": black}


def test_ceiling_1_one_game_makes_busy() -> None:
    """Default ceiling (no prefs) : historical behaviour, one game = busy."""
    available, excluded = _available_players([_live("g1", "leader", "admin")])
    assert excluded == ["admin", "leader"]
    assert set(available) == {"junior", "claude-desktop"}


def test_ceiling_2_one_game_keeps_player_available() -> None:
    prefs = {"leader": {"max_concurrent": 2}}
    available, excluded = _available_players([_live("g1", "leader", "admin")], prefs)
    assert "leader" in available
    assert excluded == ["admin"]


def test_ceiling_2_two_games_makes_busy() -> None:
    prefs = {"leader": {"max_concurrent": 2}}
    games = [_live("g1", "leader", "admin"), _live("g2", "junior", "leader")]
    available, excluded = _available_players(games, prefs)
    assert "leader" in excluded


def _write_unrated_live(games_dir: Path, game_id: str, white: str, black: str) -> None:
    text = f'[Event "Exercise"]\n[White "{white}"]\n[Black "{black}"]\n[Result "*"]\n\n1. e4 *\n'
    (games_dir / f"{game_id}.pgn").write_text(text, encoding="utf-8")


def test_unrated_exercise_never_counts(tmp_path: Path) -> None:
    """Ceiling 1 and an unrated game in progress : the player stays available."""
    _write_unrated_live(tmp_path, "exo", "claude-desktop", "leader")
    available, excluded = _available_players(_scan_active_games(tmp_path))
    assert excluded == []
    assert "leader" in available and "claude-desktop" in available


def test_schedule_round_from_disk_reads_prefs(tmp_path: Path) -> None:
    """End-to-end : the stored preference changes who is excluded."""
    _write_pgn(tmp_path, game_id="live", white="leader", black="admin", result="*")
    assert "leader" in schedule_round_from_disk(tmp_path).excluded_busy

    set_max_concurrent(tmp_path, "leader", 2)
    schedule = schedule_round_from_disk(tmp_path)
    assert "leader" not in schedule.excluded_busy
    assert "admin" in schedule.excluded_busy  # admin keeps the default ceiling 1
