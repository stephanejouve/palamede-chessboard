"""Token classes, last use and the idle browser token (#351, PR 1)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from palamede_chessboard.players import registry
from palamede_chessboard.players.registry import (
    RegistryError,
    add_player,
    issue_token,
    registry_path,
    tokens_of,
    verify,
    verify_token,
)

TODAY = date(2026, 11, 15)


@pytest.fixture(autouse=True)
def _fixed_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    monkeypatch.setattr(registry, "_today", lambda: TODAY)


def _player(games_dir: Path) -> None:
    add_player(games_dir, "human-player", kind="human")


def _entries(games_dir: Path) -> list[dict]:
    raw = json.loads(registry_path(games_dir).read_text(encoding="utf-8"))
    return raw["players"]["human-player"]["tokens"]


def _set(games_dir: Path, **fields: object) -> None:
    raw = json.loads(registry_path(games_dir).read_text(encoding="utf-8"))
    raw["players"]["human-player"]["tokens"][0].update(fields)
    registry_path(games_dir).write_text(json.dumps(raw), encoding="utf-8")


def test_a_token_without_class_is_an_api_token(tmp_path: Path) -> None:
    # Written before #351 : no class, no last_used. It keeps working.
    _player(tmp_path)
    plain = issue_token(tmp_path, "human-player", label="old")
    raw = json.loads(registry_path(tmp_path).read_text(encoding="utf-8"))
    for key in ("class", "last_used"):
        del raw["players"]["human-player"]["tokens"][0][key]
    registry_path(tmp_path).write_text(json.dumps(raw), encoding="utf-8")
    assert verify_token(tmp_path, plain) == "human-player"
    assert tokens_of(tmp_path, "human-player")[0]["class"] == "api"


def test_the_class_is_stored_and_an_unknown_one_refused(tmp_path: Path) -> None:
    _player(tmp_path)
    issue_token(tmp_path, "human-player", label="laptop", token_class="browser")
    assert _entries(tmp_path)[0]["class"] == "browser"
    with pytest.raises(RegistryError):
        issue_token(tmp_path, "human-player", label="x", token_class="admin")


@pytest.mark.parametrize("cls", ["api", "browser", "device"])
def test_the_account_classes_verify_everywhere(tmp_path: Path, cls: str) -> None:
    _player(tmp_path)
    plain = issue_token(tmp_path, "human-player", label="t", token_class=cls)
    assert verify_token(tmp_path, plain) == "human-player"


@pytest.mark.parametrize("cls", ["guest", "recovery"])
def test_guest_and_recovery_only_where_asked_by_name(tmp_path: Path, cls: str) -> None:
    # Fail-closed : a route that does not name them refuses them.
    _player(tmp_path)
    plain = issue_token(tmp_path, "human-player", label="t", token_class=cls)
    assert verify_token(tmp_path, plain) is None
    use = verify(tmp_path, plain, frozenset({cls}))
    assert use is not None and (use.player_id, use.token_class) == ("human-player", cls)


def test_last_used_is_the_day_only_and_written_once_a_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _player(tmp_path)
    plain = issue_token(tmp_path, "human-player", label="t")
    assert _entries(tmp_path)[0]["last_used"] is None
    assert verify_token(tmp_path, plain) == "human-player"
    assert _entries(tmp_path)[0]["last_used"] == "2026-11-15"
    writes = []
    monkeypatch.setattr(registry, "_mark_used", lambda *a: writes.append(a))
    verify_token(tmp_path, plain)
    assert writes == []  # already today : nothing written again


def test_a_refused_token_marks_nothing(tmp_path: Path) -> None:
    _player(tmp_path)
    plain = issue_token(tmp_path, "human-player", label="t", token_class="recovery")
    assert verify_token(tmp_path, plain) is None
    assert _entries(tmp_path)[0]["last_used"] is None


@pytest.mark.parametrize(
    ("days", "valid"),
    [(registry.BROWSER_IDLE_DAYS, True), (registry.BROWSER_IDLE_DAYS + 1, False)],
)
def test_a_browser_token_idle_90_days_expires(tmp_path: Path, days: int, valid: bool) -> None:
    _player(tmp_path)
    plain = issue_token(tmp_path, "human-player", label="t", token_class="browser")
    _set(tmp_path, last_used=(TODAY - timedelta(days=days)).isoformat())
    assert (verify_token(tmp_path, plain) == "human-player") is valid


def test_a_never_used_browser_token_counts_from_its_creation(tmp_path: Path) -> None:
    _player(tmp_path)
    plain = issue_token(tmp_path, "human-player", label="t", token_class="browser")
    _set(tmp_path, created="2026-01-01T10:00:00+00:00", last_used=None)
    assert verify_token(tmp_path, plain) is None


def test_an_unreadable_date_expires_a_browser_token(tmp_path: Path) -> None:
    _player(tmp_path)
    plain = issue_token(tmp_path, "human-player", label="t", token_class="browser")
    _set(tmp_path, created="?", last_used=None)
    assert verify_token(tmp_path, plain) is None


def test_other_classes_never_expire_from_idleness(tmp_path: Path) -> None:
    _player(tmp_path)
    plain = issue_token(tmp_path, "human-player", label="t", token_class="device")
    _set(tmp_path, last_used="2020-01-01")
    assert verify_token(tmp_path, plain) == "human-player"


def test_a_last_used_not_written_never_refuses_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _player(tmp_path)
    plain = issue_token(tmp_path, "human-player", label="t")

    def unwritable(_games_dir: Path):  # noqa: ANN202
        raise RegistryError("read-only")

    monkeypatch.setattr(registry, "_writing", unwritable)
    assert verify_token(tmp_path, plain) == "human-player"


def test_the_listed_tokens_never_show_a_digest(tmp_path: Path) -> None:
    _player(tmp_path)
    issue_token(tmp_path, "human-player", label="t", token_class="device")
    listed = tokens_of(tmp_path, "human-player")
    assert set(listed[0]) == {"id", "label", "class", "created", "last_used", "revoked"}
    assert tokens_of(tmp_path, "nobody") == []
