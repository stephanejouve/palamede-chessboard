"""Disabling and deleting an account (#362). Players named by function."""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard import cli_players
from palamede_chessboard.player_prefs import load_prefs, set_max_concurrent
from palamede_chessboard.players import accounts, registry, signup


@pytest.fixture
def gdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    monkeypatch.delenv("PALAMEDE_PLAYER_PREFS", raising=False)
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    return tmp_path


def _game(gdir: Path, white: str, black: str = "bot-maia-1100") -> None:
    (gdir / f"{white}-{black}.pgn").write_text(
        f'[White "{white}"]\n[Black "{black}"]\n[Result "*"]\n\n*\n', encoding="utf-8"
    )


def test_disable_revokes_every_token_and_keeps_the_name(
    gdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser, recovery = registry.add_player_with_tokens(
        gdir,
        "tester",
        kind="human",
        issued=[("navigateur", "browser"), ("récupération", "recovery")],
    )
    assert registry.disable_player(gdir, "tester") == 2
    assert registry.verify_token(gdir, browser) is None
    assert registry.verify(gdir, recovery, frozenset({"recovery"})) is None
    (player,) = registry.list_players(gdir)
    assert player["disabled"] and all(t["revoked"] for t in player["tokens"])
    with pytest.raises(signup.NameUnavailable):
        signup.sign_up(gdir, "tester", recovery=False, label="navigateur")
    monkeypatch.setattr(registry, "_now", lambda: "2099-01-01T00:00:00+00:00")
    assert registry.disable_player(gdir, "tester") == 0  # twice : nothing more
    assert registry.list_players(gdir)[0]["disabled"] == player["disabled"]


def test_a_disabled_account_gets_no_token_until_enabled(gdir: Path) -> None:
    registry.add_player(gdir, "tester", kind="human")
    registry.disable_player(gdir, "tester")
    with pytest.raises(registry.RegistryError, match="disabled"):
        registry.issue_token(gdir, "tester", label="cli")
    with pytest.raises(registry.RegistryError, match="disabled"):
        registry.replace_token(gdir, "tester", label="récupération", token_class="recovery")
    registry.enable_player(gdir, "tester")
    token = registry.issue_token(gdir, "tester", label="cli")
    assert registry.verify_token(gdir, token) == "tester"


def test_a_token_left_live_on_a_disabled_account_is_refused(gdir: Path) -> None:
    """Fail-closed : the account's mark alone refuses, whatever its tokens say."""
    registry.add_player(gdir, "tester", kind="human")
    token = registry.issue_token(gdir, "tester", label="cli")
    raw = registry.load(gdir)
    raw["players"]["tester"]["disabled"] = "2026-11-21T12:00:00+00:00"
    registry._save(gdir, raw)
    assert registry.verify_token(gdir, token) is None


def test_delete_frees_the_name_and_the_preferences(gdir: Path) -> None:
    registry.add_player(gdir, "tester", kind="human")
    token = registry.issue_token(gdir, "tester", label="cli")
    set_max_concurrent(gdir, "tester", 2)
    set_max_concurrent(gdir, "other", 3)
    assert accounts.delete_account(gdir, "Tester") == "tester"
    assert registry.verify_token(gdir, token) is None
    assert set(load_prefs(gdir)) == {"other"}
    player_id, _browser, _ = signup.sign_up(gdir, "tester", recovery=False, label="navigateur")
    assert player_id == "tester"


def test_an_account_a_game_names_is_not_deleted(gdir: Path) -> None:
    registry.add_player(gdir, "tester", kind="human")
    _game(gdir, "other", "Tester")
    with pytest.raises(accounts.AccountHasGames):
        accounts.delete_account(gdir, "tester")
    assert [p["player_id"] for p in registry.list_players(gdir)] == ["tester"]


def test_dry_run_writes_nothing(gdir: Path) -> None:
    registry.add_player(gdir, "tester", kind="human")
    set_max_concurrent(gdir, "tester", 2)
    accounts.delete_account(gdir, "tester", dry_run=True)
    assert [p["player_id"] for p in registry.list_players(gdir)] == ["tester"]
    assert set(load_prefs(gdir)) == {"tester"}


def test_unknown_account(gdir: Path) -> None:
    with pytest.raises(registry.RegistryError, match="unknown"):
        accounts.delete_account(gdir, "nobody")
    with pytest.raises(registry.RegistryError, match="unknown"):
        registry.disable_player(gdir, "nobody")


def test_cli_disable_enable_delete(gdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry.add_player(gdir, "tester", kind="human")
    registry.issue_token(gdir, "tester", label="cli")
    assert cli_players.main(["disable", "tester"]) == 0
    assert "1 jeton(s) révoqué(s)" in capsys.readouterr().err
    assert cli_players.main(["list"]) == 0
    assert "désactivé" in capsys.readouterr().out
    assert cli_players.main(["enable", "tester"]) == 0
    assert cli_players.main(["delete", "tester", "--dry-run"]) == 0
    assert "rien écrit" in capsys.readouterr().err
    assert cli_players.main(["delete", "tester"]) == 0
    assert registry.list_players(gdir) == []


def test_cli_refuses_to_delete_a_player_with_games(
    gdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry.add_player(gdir, "tester", kind="human")
    _game(gdir, "tester")
    assert cli_players.main(["delete", "tester"]) == 1
    assert "disable it instead" in capsys.readouterr().err
