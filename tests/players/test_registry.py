"""Native player registry (#34, step 1)."""

from __future__ import annotations

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from palamede_chessboard.players import registry
from palamede_chessboard.players.registry import (
    RegistryError,
    add_player,
    issue_token,
    list_players,
    registry_path,
    revoke_token,
    verify_token,
)


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)


def test_absent_registry_is_empty(tmp_path: Path) -> None:
    assert list_players(tmp_path) == []
    assert not registry_path(tmp_path).exists()


@pytest.mark.parametrize("raw", ["Leader", " LEADER ", "devleader"])
def test_player_id_is_normalised(tmp_path: Path, raw: str) -> None:
    assert add_player(tmp_path, raw, kind="agent")["player_id"] == "leader"


@pytest.mark.parametrize("bad", ["", "a b", "x" * 33, "../evil", ".hidden", None, 42])
def test_invalid_player_id_is_refused(tmp_path: Path, bad: object) -> None:
    with pytest.raises(RegistryError, match="invalid player id"):
        add_player(tmp_path, bad, kind="agent")


def test_duplicate_and_bad_kind_refused(tmp_path: Path) -> None:
    add_player(tmp_path, "leader", kind="agent")
    with pytest.raises(RegistryError, match="already exists"):
        add_player(tmp_path, "Leader", kind="agent")
    with pytest.raises(RegistryError, match="kind"):
        add_player(tmp_path, "junior", kind="robot")


def test_token_round_trip(tmp_path: Path) -> None:
    add_player(tmp_path, "leader", kind="agent")
    token = issue_token(tmp_path, "leader", label="Claude Code Hera")
    assert token.startswith("plm_")
    assert verify_token(tmp_path, token) == "leader"


def test_only_digest_is_stored(tmp_path: Path) -> None:
    add_player(tmp_path, "leader", kind="agent")
    token = issue_token(tmp_path, "leader", label="x")
    secret = token.split("_", 2)[2]
    text = registry_path(tmp_path).read_text(encoding="utf-8")
    assert secret not in text and token not in text
    assert "sha256" in text


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"),  # mauvais secret
        lambda t: "plm_00000000_" + t.split("_", 2)[2],  # id inconnu
        lambda t: t.replace("plm_", "xyz_"),  # mauvais préfixe
        lambda t: t[:20],  # tronqué
        lambda t: "",
    ],
)
def test_wrong_tokens_rejected(tmp_path: Path, mangle) -> None:
    add_player(tmp_path, "leader", kind="agent")
    token = issue_token(tmp_path, "leader", label="x")
    assert verify_token(tmp_path, mangle(token)) is None


def test_non_string_token_rejected(tmp_path: Path) -> None:
    assert verify_token(tmp_path, None) is None
    assert verify_token(tmp_path, 42) is None


def test_revoked_token_rejected_and_kept_for_audit(tmp_path: Path) -> None:
    add_player(tmp_path, "leader", kind="agent")
    token = issue_token(tmp_path, "leader", label="old")
    token_id = token.split("_")[1]
    revoke_token(tmp_path, "leader", token_id)
    assert verify_token(tmp_path, token) is None
    [entry] = list_players(tmp_path)[0]["tokens"]
    assert entry["id"] == token_id and entry["revoked"]


def test_revoke_unknown_token_refused(tmp_path: Path) -> None:
    add_player(tmp_path, "leader", kind="agent")
    with pytest.raises(RegistryError, match="no token"):
        revoke_token(tmp_path, "leader", "deadbeef")


def test_token_of_one_player_does_not_open_another(tmp_path: Path) -> None:
    add_player(tmp_path, "leader", kind="agent")
    add_player(tmp_path, "junior", kind="agent")
    token = issue_token(tmp_path, "junior", label="x")
    assert verify_token(tmp_path, token) == "junior"


def test_public_view_never_exposes_digest(tmp_path: Path) -> None:
    add_player(tmp_path, "leader", kind="agent")
    issue_token(tmp_path, "leader", label="x")
    assert "sha256" not in json.dumps(list_players(tmp_path))


def test_file_and_dir_permissions(tmp_path: Path) -> None:
    add_player(tmp_path, "leader", kind="agent")
    path = registry_path(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("content", ["{not json", "[]", '{"players": []}'])
def test_unreadable_registry_raises_never_empty(tmp_path: Path, content: str) -> None:
    """Fail-closed : a broken registry must not read as « no players »."""
    path = registry_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RegistryError):
        verify_token(tmp_path, "plm_00000000_" + "a" * 43)
    with pytest.raises(RegistryError):
        add_player(tmp_path, "leader", kind="agent")
    assert path.read_text(encoding="utf-8") == content


def test_concurrent_writes_keep_every_update(tmp_path: Path) -> None:
    add_player(tmp_path, "leader", kind="agent")
    with ThreadPoolExecutor(max_workers=8) as pool:
        issued = list(pool.map(lambda i: issue_token(tmp_path, "leader", label=f"t{i}"), range(16)))
    assert len(list_players(tmp_path)[0]["tokens"]) == 16
    assert all(verify_token(tmp_path, t) == "leader" for t in issued)


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "ailleurs" / "registre.json"
    monkeypatch.setenv(registry.REGISTRY_ENV, str(custom))
    add_player(tmp_path / "games", "leader", kind="agent")
    assert custom.exists()
