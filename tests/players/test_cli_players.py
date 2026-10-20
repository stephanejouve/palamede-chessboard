"""``palamede-players`` CLI (#34)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from palamede_chessboard import cli_players
from palamede_chessboard.players import registry


@pytest.fixture
def gdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    return tmp_path


def test_add_list_show(gdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_players.main(["add", "Leader", "--kind", "agent", "--name", "Leader"]) == 0
    assert json.loads(capsys.readouterr().out)["player_id"] == "leader"
    assert cli_players.main(["list"]) == 0
    assert "leader" in capsys.readouterr().out
    assert cli_players.main(["show", "devleader"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "agent"


def test_token_to_file_is_0600_and_never_printed(
    gdir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_players.main(["add", "leader", "--kind", "agent"])
    capsys.readouterr()
    out = tmp_path / "leader.token"
    assert cli_players.main(["token", "leader", "--label", "Hera", "--out", str(out)]) == 0
    token = out.read_text(encoding="utf-8").strip()
    captured = capsys.readouterr()
    assert token not in captured.out and token not in captured.err
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert registry.verify_token(gdir, token) == "leader"


def test_token_refuses_existing_file_and_issues_nothing(gdir: Path, tmp_path: Path) -> None:
    cli_players.main(["add", "leader", "--kind", "agent"])
    out = tmp_path / "existe.token"
    out.write_text("ancien\n", encoding="utf-8")
    assert cli_players.main(["token", "leader", "--label", "x", "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "ancien\n"
    assert registry.list_players(gdir)[0]["tokens"] == []


def test_token_requires_explicit_destination(gdir: Path) -> None:
    cli_players.main(["add", "leader", "--kind", "agent"])
    with pytest.raises(SystemExit):
        cli_players.main(["token", "leader", "--label", "x"])


def test_token_stdout_only_when_asked(gdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli_players.main(["add", "leader", "--kind", "agent"])
    capsys.readouterr()
    assert cli_players.main(["token", "leader", "--label", "x", "--stdout"]) == 0
    token = capsys.readouterr().out.strip()
    assert registry.verify_token(gdir, token) == "leader"


def test_revoke_and_errors(gdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli_players.main(["add", "leader", "--kind", "agent"])
    cli_players.main(["token", "leader", "--label", "x", "--stdout"])
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert cli_players.main(["revoke", "leader", token.split("_")[1]]) == 0
    assert registry.verify_token(gdir, token) is None
    assert cli_players.main(["show", "inconnu"]) == 1
    assert "unknown player" in capsys.readouterr().err


# ---- revue Junior #54 : aucun jeton orphelin ------------------------------


def test_out_in_missing_dir_issues_nothing(gdir: Path, tmp_path: Path) -> None:
    cli_players.main(["add", "leader", "--kind", "agent"])
    out = tmp_path / "absent" / "leader.jeton"
    assert cli_players.main(["token", "leader", "--label", "x", "--out", str(out)]) == 1
    assert registry.list_players(gdir)[0]["tokens"] == []


def test_failed_issue_removes_the_empty_file(gdir: Path, tmp_path: Path) -> None:
    out = tmp_path / "inconnu.jeton"
    assert cli_players.main(["token", "inconnu", "--label", "x", "--out", str(out)]) == 1
    assert not out.exists()
