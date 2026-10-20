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


@pytest.mark.parametrize("corrupt", ['{"players": {"leader": {"max_concurrent": 2},}}', "[]"])
def test_set_refuses_to_overwrite_unreadable_file(tmp_path: Path, corrupt: str) -> None:
    """Review Junior #40 : an unreadable file must not be rewritten from scratch,
    or every other player's preference would be lost silently."""
    path = tmp_path / PREFS_FILENAME
    path.write_text(corrupt, encoding="utf-8")
    with pytest.raises(PlayerPrefsError, match="nothing written"):
        set_max_concurrent(tmp_path, "junior", 2)
    assert path.read_text(encoding="utf-8") == corrupt  # left intact


def test_set_keeps_unknown_keys_and_mode(tmp_path: Path) -> None:
    path = tmp_path / PREFS_FILENAME
    path.write_text(
        '{"version": 1, "players": {"admin": {"max_concurrent": 3, "note": "x"}, "leader": 5}}',
        encoding="utf-8",
    )
    path.chmod(0o640)
    set_max_concurrent(tmp_path, "leader", 2)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == 1
    assert on_disk["players"]["admin"] == {"max_concurrent": 3, "note": "x"}
    assert on_disk["players"]["leader"] == {"max_concurrent": 2}
    assert path.stat().st_mode & 0o777 == 0o640


def test_new_file_is_world_readable(tmp_path: Path) -> None:
    set_max_concurrent(tmp_path, "leader", 2)
    assert (tmp_path / PREFS_FILENAME).stat().st_mode & 0o777 == 0o644


def test_concurrent_sets_keep_every_update(tmp_path: Path) -> None:
    """Review Junior #40 : sets in parallel threads must not drop each other."""
    from concurrent.futures import ThreadPoolExecutor

    players = [f"player{i}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda p: set_max_concurrent(tmp_path, p, 2), players))
    prefs = load_prefs(tmp_path)
    assert all(max_concurrent_for(prefs, p) == 2 for p in players)


def test_env_override_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "elsewhere" / "prefs.json"
    monkeypatch.setenv(PREFS_ENV, str(custom))
    set_max_concurrent(tmp_path / "games", "junior", 2)
    assert custom.exists()
    assert max_concurrent_for(load_prefs(tmp_path / "games"), "junior") == 2


def _write(tmp_path: Path, players: str) -> Path:
    path = tmp_path / PREFS_FILENAME
    path.write_text('{"players": ' + players + "}", encoding="utf-8")
    return path


@pytest.mark.parametrize("key", ["Leader", " LEADER ", "devleader"])
def test_hand_edited_key_is_normalised(tmp_path: Path, key: str) -> None:
    """Revue CD #40 : une clé saisie à la main n'est pas ignorée en silence."""
    _write(tmp_path, json.dumps({key: {"max_concurrent": 3}}))
    assert max_concurrent_for(load_prefs(tmp_path), "leader") == 3


@pytest.mark.parametrize(
    ("players", "expected"),
    [
        # la clé déjà canonique l'emporte, quel que soit l'ordre
        ('{"Leader": {"max_concurrent": 2}, "leader": {"max_concurrent": 3}}', 3),
        ('{"leader": {"max_concurrent": 3}, "devleader": {"max_concurrent": 2}}', 3),
        # entre deux clés non canoniques : la première dans le fichier
        ('{"Leader": {"max_concurrent": 2}, "LEADER": {"max_concurrent": 3}}', 2),
        # une entrée dict l'emporte sur une entrée invalide, même canonique
        ('{"leader": 5, "Leader": {"max_concurrent": 2}}', 2),
    ],
)
def test_collision_is_deterministic_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, players: str, expected: int
) -> None:
    _write(tmp_path, players)
    with caplog.at_level("WARNING"):
        prefs = load_prefs(tmp_path)
    assert max_concurrent_for(prefs, "leader") == expected
    assert "both map to 'leader'" in caplog.text


def test_set_merges_duplicate_keys(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '{"Leader": {"max_concurrent": 2, "note": "x"}, "admin": {"max_concurrent": 4}}',
    )
    set_max_concurrent(tmp_path, "leader", 3)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["players"] == {
        "admin": {"max_concurrent": 4},
        "leader": {"max_concurrent": 3, "note": "x"},
    }


def test_empty_key_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path, '{"  ": {"max_concurrent": 3}, "junior": {"max_concurrent": 2}}')
    assert load_prefs(tmp_path) == {"junior": {"max_concurrent": 2}}
