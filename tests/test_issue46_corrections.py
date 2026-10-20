"""Corrections of #46 (follow-ups of #27 §5)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from palamede_chessboard.chess_engine_guard import check_engine_call
from palamede_chessboard.chessboard_clock import ClockState, save_clock
from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameMetadata,
    GameState,
    TimeControl,
)
from palamede_chessboard.paths import _validate_game_id
from palamede_chessboard.player_prefs import set_max_concurrent

# ---- pendule : pas d'incrément après la chute du drapeau -------------------


def _clock() -> ClockState:
    clock = ClockState.init_from_time_control("g", TimeControl(format="5+3"))
    clock.start(now=1000.0)
    return clock


def test_no_increment_when_black_flag_falls() -> None:
    clock = _clock()
    clock.on_move_played(mover="white", now=1001.0)
    clock.on_move_played(mover="black", now=1400.0)  # 399 s > 300
    assert clock.flag_fall == "black"
    assert clock.black_seconds == 300 - 399


def test_flag_exactly_at_zero_gets_no_increment() -> None:
    clock = _clock()
    clock.on_move_played(mover="white", now=1300.0)  # exactly 300 s
    assert clock.flag_fall == "white"
    assert clock.white_seconds == 0


def test_increment_still_applies_in_time() -> None:
    clock = _clock()
    clock.on_move_played(mover="white", now=1299.0)  # 1 s left
    assert clock.flag_fall is None
    assert clock.white_seconds == 1 + 3


# ---- game_id : pas de point en tête -------------------------------------


@pytest.mark.parametrize("bad", [".", "..", "...", ".hidden", ".player_prefs"])
def test_game_id_with_leading_dot_is_refused(bad: str) -> None:
    with pytest.raises(ChessboardError, match="Invalid game_id"):
        _validate_game_id(bad)


@pytest.mark.parametrize("good", ["exo-stephane-001", "a.b", "x..y", "g_1", "a" * 64])
def test_ordinary_game_ids_still_accepted(good: str) -> None:
    _validate_game_id(good)


def test_game_id_length_limit_unchanged() -> None:
    with pytest.raises(ChessboardError):
        _validate_game_id("a" * 65)


# ---- garde moteur : verdict « autorisé » non partagé ----------------------


@pytest.mark.parametrize(
    "call",
    [
        {"tool": "chess_list_games"},  # outil non surveillé : retour immédiat
        {"tool": "chess_analyze_position", "fen": "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"},
    ],
)
def test_allowed_verdict_is_not_shared(tmp_path: Path, call: dict) -> None:
    first = check_engine_call(tmp_path, **call)
    assert first["allowed"] is True
    first["allowed"] = False  # a careless caller mutates its verdict
    second = check_engine_call(tmp_path, **call)
    assert second == {"allowed": True, "matched_game_id": None, "reason": None}
    assert second is not first


# ---- fsync avant le remplacement atomique --------------------------------


@pytest.fixture
def fsync_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = os.fsync

    def spy(fd: int) -> None:
        calls.append(fd)
        real(fd)

    monkeypatch.setattr(os, "fsync", spy)
    return calls


def test_pgn_save_fsyncs(tmp_path: Path, fsync_calls: list[int]) -> None:
    GameState.create(GameMetadata(game_id="g", white="w", black="b"), games_dir=tmp_path)
    assert fsync_calls
    assert not list(tmp_path.glob("*.tmp"))


def test_clock_save_fsyncs(tmp_path: Path, fsync_calls: list[int]) -> None:
    save_clock(_clock(), games_dir=tmp_path)
    assert fsync_calls


def test_prefs_save_fsyncs(
    tmp_path: Path, fsync_calls: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PALAMEDE_PLAYER_PREFS", raising=False)
    set_max_concurrent(tmp_path, "leader", 2)
    assert fsync_calls
