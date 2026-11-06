"""#246 : the four writers with their own replace chain fsync the parent directory.

``_atomic.write_json`` already did ; the PGN, clock, player prefs and Elo cache
writers replaced the file without making the rename itself durable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard import chessboard_clock, chessboard_state, player_prefs
from palamede_chessboard.chess_elo import cache as elo_cache
from palamede_chessboard.chess_elo import rebuild_ratings_from_games_dir
from palamede_chessboard.chessboard_clock import ClockState, save_clock
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.player_prefs import set_max_concurrent


def _spy(monkeypatch: pytest.MonkeyPatch, module: object) -> list[tuple[Path, list[str]]]:
    """Record each fsync_dir call with the directory listing at that moment."""
    calls: list[tuple[Path, list[str]]] = []

    def spy(directory: Path) -> None:
        calls.append((directory, sorted(p.name for p in directory.iterdir())))

    monkeypatch.setattr(module, "fsync_dir", spy)
    return calls


def test_pgn_save_fsyncs_parent_after_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, chessboard_state)
    GameState.create(GameMetadata(game_id="g", white="w", black="b"), games_dir=tmp_path)
    assert calls and calls[-1][0] == tmp_path
    assert not any(name.endswith(".tmp") for name in calls[-1][1])


def test_clock_save_fsyncs_parent_after_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, chessboard_clock)
    save_clock(
        ClockState.init_from_time_control("g", TimeControl(format="5+3")), games_dir=tmp_path
    )
    assert calls == [(tmp_path, ["clock_g.json"])]


def test_prefs_save_fsyncs_parent_after_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PALAMEDE_PLAYER_PREFS", raising=False)
    calls = _spy(monkeypatch, player_prefs)
    set_max_concurrent(tmp_path, "leader", 2)
    path = player_prefs.prefs_path(tmp_path)
    assert len(calls) == 1 and calls[0][0] == path.parent
    assert path.name in calls[0][1]
    assert not any(name.endswith(".tmp") for name in calls[0][1])


def test_elo_cache_save_fsyncs_parent_after_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, elo_cache)
    path = elo_cache.save_cached_ratings(
        rebuild_ratings_from_games_dir(games_dir=tmp_path), games_dir=tmp_path
    )
    assert len(calls) == 1 and calls[0][0] == path.parent
    assert path.name in calls[0][1]
    assert not any(name.endswith(".tmp") for name in calls[0][1])
