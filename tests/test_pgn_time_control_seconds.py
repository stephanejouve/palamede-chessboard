"""``TimeControl`` is written in seconds, as the PGN standard counts it (#151).

The games written before held minutes (``15+30``) : a third-party tool read
them as 15 seconds. The writer now puts seconds (``900+30``) and, beside
them, ``TimeControlUnit "seconds"`` : ``60+0`` is one minute in seconds and
an hour in the old minutes, so only the marker tells them apart. A game
without it keeps reading as before.
"""

from __future__ import annotations

from pathlib import Path

import chess
import pytest

from palamede_chessboard.chessboard_clock import parse_time_control
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.pgn_io import pgn_text, time_control_from_headers


def _meta(tc: TimeControl) -> GameMetadata:
    return GameMetadata(game_id="tc", white="w", black="b", time_control=tc, date="2026.11.04")


def _headers(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("[Time")]


def _legacy(tmp_path: Path, time_control: str | None, marker: bool = False) -> Path:
    tags = ['[White "w"]', '[Black "b"]', '[Result "1-0"]']
    if time_control is not None:
        tags.append(f'[TimeControl "{time_control}"]')
    if marker:
        tags.append('[TimeControlUnit "seconds"]')
    path = tmp_path / "tc.pgn"
    path.write_text("\n".join(tags) + "\n\n1. e4 e5 1-0\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("tc", "expected"),
    [
        (TimeControl(format="15+30"), "900+30"),
        (TimeControl(format="1+0"), "60+0"),
        (TimeControl(format="unrated", start_seconds=90, increment_seconds=5), "90+5"),
    ],
)
def test_a_clock_is_written_in_seconds_with_its_marker(tc: TimeControl, expected: str) -> None:
    text = pgn_text(_meta(tc), chess.Board(), [])
    assert _headers(text) == [f'[TimeControl "{expected}"]', '[TimeControlUnit "seconds"]']


def test_a_game_without_clock_says_so_with_a_dash() -> None:
    text = pgn_text(_meta(TimeControl(format="unrated")), chess.Board(), [])
    assert _headers(text) == ['[TimeControl "-"]']


def test_a_label_no_clock_reads_is_kept_without_marker() -> None:
    text = pgn_text(_meta(TimeControl(format="40/7200:0+10")), chess.Board(), [])
    assert _headers(text) == ['[TimeControl "40/7200:0+10"]']


@pytest.mark.parametrize(
    "tc",
    [
        TimeControl(format="15+30"),
        TimeControl(format="1+0"),
        TimeControl(format="60+0"),
        TimeControl(format="unrated"),
        TimeControl(format="unrated", start_seconds=90, increment_seconds=5),
    ],
)
def test_the_clock_survives_save_and_load(tmp_path: Path, tc: TimeControl) -> None:
    state = GameState.create(_meta(tc), games_dir=tmp_path)
    first = state.raw_pgn()
    loaded = GameState.load("tc", games_dir=tmp_path)
    assert parse_time_control(loaded.metadata.time_control) == parse_time_control(tc)
    loaded.save()
    assert loaded.raw_pgn() == first


def test_one_minute_and_one_hour_stay_apart(tmp_path: Path) -> None:
    GameState.create(_meta(TimeControl(format="1+0")), games_dir=tmp_path)
    assert parse_time_control(GameState.load("tc", games_dir=tmp_path).metadata.time_control) == (
        60,
        0,
    )
    _legacy(tmp_path, "60+0")
    assert parse_time_control(GameState.load("tc", games_dir=tmp_path).metadata.time_control) == (
        3600,
        0,
    )


def test_a_game_written_before_still_reads_minutes(tmp_path: Path) -> None:
    _legacy(tmp_path, "15+30")
    state = GameState.load("tc", games_dir=tmp_path)
    assert state.metadata.time_control.format == "15+30"
    state.save()
    assert _headers(state.raw_pgn()) == ['[TimeControl "900+30"]', '[TimeControlUnit "seconds"]']


def test_an_imported_standard_time_control_loads_without_inventing_a_clock(
    tmp_path: Path,
) -> None:
    # The form of the three imported games found in production.
    _legacy(tmp_path, "40/7200:0+10")
    state = GameState.load("tc", games_dir=tmp_path)
    assert parse_time_control(state.metadata.time_control) is None
    state.save()
    assert _headers(state.raw_pgn()) == ['[TimeControl "40/7200:0+10"]']


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, None),
        ({"TimeControl": "-"}, None),
        ({"TimeControl": "900+30", "TimeControlUnit": "seconds"}, (900, 30)),
        ({"TimeControl": "90+5", "TimeControlUnit": "seconds"}, (90, 5)),
        ({"TimeControl": "15+30"}, (900, 30)),
        ({"TimeControl": "15+30", "TimeControlUnit": "minutes"}, (900, 30)),
        ({"TimeControl": "unlimited", "TimeControlUnit": "seconds"}, None),
    ],
)
def test_time_control_from_headers(headers: dict, expected) -> None:
    assert parse_time_control(time_control_from_headers(headers)) == expected


@pytest.mark.parametrize("fmt", ["15+30", "1+0", "unrated"])
def test_the_game_reads_back_the_time_control_it_was_created_with(tmp_path: Path, fmt: str) -> None:
    # The viewer shows the format : ``15+30`` comes back as ``15+30``, not
    # ``900+30``, and a game without clock as ``unrated``, not ``-``.
    GameState.create(_meta(TimeControl(format=fmt)), games_dir=tmp_path)
    assert GameState.load("tc", games_dir=tmp_path).metadata.time_control == TimeControl(format=fmt)
