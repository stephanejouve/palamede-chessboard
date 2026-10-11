"""Tests for ``palamede_chessboard.chessboard_clock``."""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.chessboard_clock import (
    ClockState,
    load_clock,
    parse_time_control,
    result_from_flag_fall,
    save_clock,
)
from palamede_chessboard.chessboard_state import TimeControl


class TestParseTimeControl:
    def test_unrated_returns_none(self) -> None:
        assert parse_time_control(TimeControl(format="unrated")) is None

    def test_format_5_plus_3(self) -> None:
        assert parse_time_control(TimeControl(format="5+3")) == (300, 3)

    def test_format_15_plus_10(self) -> None:
        assert parse_time_control(TimeControl(format="15+10")) == (900, 10)

    def test_explicit_fields_win_over_format(self) -> None:
        tc = TimeControl(format="unrated", start_seconds=600, increment_seconds=5)
        assert parse_time_control(tc) == (600, 5)

    def test_unparseable_format_returns_none(self) -> None:
        assert parse_time_control(TimeControl(format="garbage")) is None

    def test_extra_whitespace_tolerated(self) -> None:
        assert parse_time_control(TimeControl(format="  5 + 3  ")) == (300, 3)


class TestClockInit:
    def test_init_from_unrated_returns_none(self) -> None:
        clock = ClockState.init_from_time_control("g", TimeControl(format="unrated"))
        assert clock is None

    def test_init_from_5_plus_3(self) -> None:
        clock = ClockState.init_from_time_control("g", TimeControl(format="5+3"))
        assert clock is not None
        assert clock.white_seconds == 300
        assert clock.black_seconds == 300
        assert clock.increment_seconds == 3
        assert clock.active is None
        assert clock.started is False


class TestClockProgression:
    def _clock(self) -> ClockState:
        return ClockState.init_from_time_control("g", TimeControl(format="5+3"))

    def test_start_activates_white(self) -> None:
        clock = self._clock()
        clock.start(now=1000.0)
        assert clock.active == "white"
        assert clock.last_move_ts == 1000.0
        assert clock.started is True

    def test_white_move_decrements_and_increments(self) -> None:
        clock = self._clock()
        clock.start(now=1000.0)
        clock.on_move_played(mover="white", now=1010.0)  # 10s elapsed
        # 300 - 10 + 3 = 293
        assert clock.white_seconds == 293
        assert clock.black_seconds == 300
        assert clock.active == "black"

    def test_alternating_moves(self) -> None:
        clock = self._clock()
        clock.start(now=1000.0)
        clock.on_move_played(mover="white", now=1010.0)  # white -10+3 = 293
        clock.on_move_played(mover="black", now=1020.0)  # black -10+3 = 293
        clock.on_move_played(mover="white", now=1025.0)  # white -5+3 = 291
        assert clock.white_seconds == 291
        assert clock.black_seconds == 293
        assert clock.active == "black"

    def test_no_op_when_not_started(self) -> None:
        clock = self._clock()
        before = clock.white_seconds
        clock.on_move_played(mover="white", now=1000.0)
        assert clock.white_seconds == before
        assert clock.active is None

    def test_flag_fall_on_white(self) -> None:
        clock = self._clock()
        clock.start(now=1000.0)
        clock.on_move_played(mover="white", now=2000.0)  # 1000s elapsed > 300
        assert clock.flag_fall == "white"
        assert clock.white_seconds < 0 or clock.white_seconds <= 3  # fischer +3 still applied

    def test_flag_fall_on_black(self) -> None:
        clock = self._clock()
        clock.start(now=1000.0)
        clock.on_move_played(mover="white", now=1001.0)
        clock.on_move_played(mover="black", now=2000.0)
        assert clock.flag_fall == "black"

    def test_unknown_mover_raises(self) -> None:
        clock = self._clock()
        clock.start(now=1000.0)
        with pytest.raises(ValueError):
            clock.on_move_played(mover="green", now=1001.0)

    def test_relay_floor_absorbs_short_drift(self) -> None:
        # Talk-mediated arbitrage : a 25s wall-time drift with
        # ``relay_floor=30`` consumes 0s of clock — only the +3 Fischer
        # increment is applied. Avoids charging relay latency to the player.
        clock = self._clock()
        clock.start(now=1000.0)
        clock.on_move_played(mover="white", now=1025.0, relay_floor=30.0)
        assert clock.white_seconds == 303.0  # 300 - 0 + 3

    def test_relay_floor_charges_overage(self) -> None:
        # 45s wall-time drift with ``relay_floor=30`` → effective 15s
        # think → 300 - 15 + 3 = 288s.
        clock = self._clock()
        clock.start(now=1000.0)
        clock.on_move_played(mover="white", now=1045.0, relay_floor=30.0)
        assert clock.white_seconds == 288.0

    def test_relay_floor_zero_keeps_legacy_behavior(self) -> None:
        # Default ``relay_floor=0`` → strict Fischer (every wall-time
        # second of drift counts), unchanged from pre-fix behavior.
        clock = self._clock()
        clock.start(now=1000.0)
        clock.on_move_played(mover="white", now=1010.0)
        assert clock.white_seconds == 293.0  # 300 - 10 + 3

    def test_relay_floor_negative_treated_as_zero(self) -> None:
        # Defensive : a negative ``relay_floor`` is clamped to 0.
        clock = self._clock()
        clock.start(now=1000.0)
        clock.on_move_played(mover="white", now=1010.0, relay_floor=-50.0)
        assert clock.white_seconds == 293.0


class TestPersistence:
    def test_save_then_load_roundtrip(self, tmp_path: Path) -> None:
        clock = ClockState.init_from_time_control("round-trip", TimeControl(format="5+3"))
        clock.start(now=1234.5)
        clock.on_move_played(mover="white", now=1244.5)
        save_clock(clock, games_dir=tmp_path)

        loaded = load_clock("round-trip", games_dir=tmp_path)
        assert loaded is not None
        assert loaded.white_seconds == clock.white_seconds
        assert loaded.black_seconds == clock.black_seconds
        assert loaded.active == "black"
        assert loaded.flag_fall is None

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert load_clock("nope", games_dir=tmp_path) is None


class TestResultMapping:
    def test_white_flag_fall_means_black_wins(self) -> None:
        assert result_from_flag_fall("white") == "0-1"

    def test_black_flag_fall_means_white_wins(self) -> None:
        assert result_from_flag_fall("black") == "1-0"

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            result_from_flag_fall("draw?")
