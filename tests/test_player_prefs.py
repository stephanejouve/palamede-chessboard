"""Per-player preferences store (issue #38)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from palamede_chessboard.player_prefs import (
    DEFAULT_MAX_CONCURRENT,
    PREFS_ENV,
    PREFS_FILENAME,
    PlayerPrefsError,
    load_prefs,
    max_concurrent_for,
    set_max_concurrent,
)


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PREFS_ENV, raising=False)


def test_absent_file_means_defaults(tmp_path: Path) -> None:
    assert load_prefs(tmp_path) == {}
    assert max_concurrent_for({}, "leader") == DEFAULT_MAX_CONCURRENT == 1


@pytest.mark.parametrize("content", ["{not json", "[]", '{"players": []}', '"x"'])
def test_malformed_file_falls_back_to_defaults(tmp_path: Path, content: str) -> None:
    (tmp_path / PREFS_FILENAME).write_text(content, encoding="utf-8")
    assert load_prefs(tmp_path) == {}


@pytest.mark.parametrize("stored", [0, 11, -1, "2", True, None, 1.5])
def test_out_of_range_stored_value_never_widens_ceiling(stored) -> None:
    assert max_concurrent_for({"leader": {"max_concurrent": stored}}, "leader") == 1


def test_lookup_normalises_identity() -> None:
    prefs = {"leader": {"max_concurrent": 3}}
    assert max_concurrent_for(prefs, "Leader") == 3
    assert max_concurrent_for(prefs, "devleader") == 3  # historical alias


def test_set_persists_and_keeps_other_players(tmp_path: Path) -> None:
    set_max_concurrent(tmp_path, "admin", 2)
    saved = set_max_concurrent(tmp_path, "Leader", 3)
    assert saved == {"player": "leader", "max_concurrent": 3}
    prefs = load_prefs(tmp_path)
    assert max_concurrent_for(prefs, "admin") == 2
    assert max_concurrent_for(prefs, "leader") == 3
    on_disk = json.loads((tmp_path / PREFS_FILENAME).read_text(encoding="utf-8"))
    assert on_disk == {"players": {"admin": {"max_concurrent": 2}, "leader": {"max_concurrent": 3}}}
    assert not list(tmp_path.glob("*.tmp"))  # atomic write leaves no temp file


@pytest.mark.parametrize("bad", [0, 11, "2", True, None, 2.0])
def test_set_refuses_invalid_value(tmp_path: Path, bad) -> None:
    with pytest.raises(PlayerPrefsError, match="max_concurrent"):
        set_max_concurrent(tmp_path, "leader", bad)
    assert not (tmp_path / PREFS_FILENAME).exists()


@pytest.mark.parametrize("bad_player", ["", "   ", None, 42])
def test_set_refuses_invalid_player(tmp_path: Path, bad_player) -> None:
    with pytest.raises(PlayerPrefsError, match="invalid player"):
        set_max_concurrent(tmp_path, bad_player, 2)


def test_env_override_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "elsewhere" / "prefs.json"
    monkeypatch.setenv(PREFS_ENV, str(custom))
    set_max_concurrent(tmp_path / "games", "junior", 2)
    assert custom.exists()
    assert max_concurrent_for(load_prefs(tmp_path / "games"), "junior") == 2
