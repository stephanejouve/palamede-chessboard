"""Observation mode of player authentication (#34, step 2)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from palamede_chessboard import mcp_chessboard_server as srv
from palamede_chessboard.chessboard_state import GameMetadata, GameState
from palamede_chessboard.players import observe as obs
from palamede_chessboard.players import registry

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def gdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    monkeypatch.setenv(obs.AUTH_MODE_ENV, "observe")
    for pid in ("leader", "junior", "admin", "cd"):
        registry.add_player(tmp_path, pid, kind="agent")
    GameState.create(
        GameMetadata(
            game_id="g", white="Leader", black="junior", arbiter="admin", spectators=["cd"]
        ),
        games_dir=tmp_path,
    )
    return tmp_path


def _journal(gdir: Path) -> list[dict]:
    path = registry.registry_path(gdir).parent / obs.OBSERVATIONS_FILENAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _roles(gdir: Path) -> dict | None:
    return obs._game_roles("g", gdir)


# ---- règles de #34 §2 ------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args", "who", "expected"),
    [
        ("chess_play_move", {"game_id": "g"}, "leader", None),  # Blancs au trait
        ("chess_play_move", {"game_id": "g"}, "junior", "NOT_YOUR_TURN"),
        ("chess_play_move", {"game_id": "g"}, "admin", "NOT_A_PLAYER"),  # l'arbitre ne joue pas
        ("chess_resign", {"game_id": "g"}, "junior", None),
        ("chess_resign", {"game_id": "g"}, "cd", "NOT_A_PLAYER"),
        ("chess_offer_draw", {"game_id": "g"}, "leader", None),
        ("chess_clock_start", {"game_id": "g"}, "admin", None),
        ("chess_clock_start", {"game_id": "g"}, "cd", "NOT_A_PLAYER"),
        ("chess_post_commentary", {"game_id": "g"}, "cd", None),  # spectateur déclaré
        ("chess_post_commentary", {"game_id": "g"}, "inconnu", "NOT_ALLOWED"),
        ("chess_view_state", {"game_id": "g"}, "cd", None),  # lecture
        ("chess_view_state", {"game_id": "g"}, None, "UNAUTHENTICATED"),
    ],
)
def test_game_rules(gdir: Path, tool: str, args: dict, who: str | None, expected) -> None:
    assert obs.evaluate(tool, args, who, _roles(gdir)) == expected


@pytest.mark.parametrize(
    ("args", "who", "expected"),
    [
        ({"white": "Leader", "black": "junior"}, "leader", None),
        ({"white": "a", "black": "b", "arbiter": "admin"}, "admin", None),
        ({"white": "a", "black": "b"}, "leader", "NOT_A_PLAYER"),
    ],
)
def test_create_game_rule(args: dict, who: str, expected) -> None:
    assert obs.evaluate("chess_create_game", args, who, None) == expected


def test_own_account_rule() -> None:
    rule = "chess_set_player_preferences"
    assert obs.evaluate(rule, {"player": "devleader"}, "leader", None) is None
    assert obs.evaluate(rule, {"player": "junior"}, "leader", None) == "NOT_YOUR_ACCOUNT"


def test_unknown_game_is_left_to_the_handler() -> None:
    assert obs.evaluate("chess_play_move", {"game_id": "x"}, "leader", None) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Bearer abc", "abc"), ("bearer  abc ", "abc"), ("Basic abc", None), ("Bearer", None)],
)
def test_bearer_parsing(value: str, expected) -> None:
    assert obs.bearer_token({"authorization": value}) == expected
    assert obs.bearer_token({}) is None
    assert obs.bearer_token(None) is None


# ---- journal ---------------------------------------------------------------


def test_mode_off_writes_nothing(gdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(obs.AUTH_MODE_ENV, "off")
    assert obs.observe("chess_view_state", {"game_id": "g"}, {}, gdir, transport="http") is None
    monkeypatch.setenv(obs.AUTH_MODE_ENV, "strict")  # pas encore pris en charge
    assert obs.observe("chess_view_state", {"game_id": "g"}, {}, gdir, transport="http") is None
    assert _journal(gdir) == []


def test_journal_entry_never_contains_the_token(gdir: Path) -> None:
    token = registry.issue_token(gdir, "junior", label="t")
    headers = {"authorization": f"Bearer {token}"}
    entry = obs.observe(
        "chess_play_move", {"game_id": "g", "by": "black"}, headers, gdir, transport="http"
    )
    assert entry is not None
    assert (entry["caller"], entry["auth"], entry["code"]) == ("junior", "ok", "NOT_YOUR_TURN")
    assert entry["by_matches"] is True  # « black » = junior dans le PGN
    path = registry.registry_path(gdir).parent / obs.OBSERVATIONS_FILENAME
    assert token.split("_", 2)[2] not in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_by_mismatch_is_flagged(gdir: Path) -> None:
    token = registry.issue_token(gdir, "leader", label="t")
    entry = obs.observe(
        "chess_play_move",
        {"game_id": "g", "by": "junior"},
        {"authorization": f"Bearer {token}"},
        gdir,
        transport="http",
    )
    assert entry is not None and entry["by_matches"] is False and entry["verdict"] == "allow"


@pytest.mark.parametrize(
    ("headers", "auth"),
    [({}, "missing"), ({"authorization": "Bearer plm_00000000_" + "a" * 43}, "invalid")],
)
def test_unauthenticated_calls(gdir: Path, headers: dict, auth: str) -> None:
    entry = obs.observe("chess_view_state", {"game_id": "g"}, headers, gdir, transport="http")
    assert entry is not None and (entry["auth"], entry["code"]) == (auth, "UNAUTHENTICATED")


def test_unreadable_registry_is_logged_not_raised(gdir: Path) -> None:
    registry.registry_path(gdir).write_text("{cassé", encoding="utf-8")
    entry = obs.observe(
        "chess_view_state",
        {"game_id": "g"},
        {"authorization": "Bearer plm_00000000_" + "a" * 43},
        gdir,
        transport="http",
    )
    assert entry is not None and entry["code"] == "REGISTRY_UNREADABLE"


def test_observation_failure_never_breaks_the_call(
    gdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a, **_k):
        raise OSError("disque plein")

    monkeypatch.setattr(obs, "_append", boom)
    assert obs.observe("chess_view_state", {"game_id": "g"}, {}, gdir, transport="http") is None


# ---- de bout en bout, transport HTTP ---------------------------------------


def _call(client: TestClient, name: str, arguments: dict, token: str | None) -> dict:
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    resp = client.post("/mcp/", headers=headers, json=body)
    assert resp.status_code == 200
    text = resp.text
    data = (
        resp.json()
        if text.lstrip().startswith("{")
        else json.loads(next(line[5:] for line in text.splitlines() if line.startswith("data:")))
    )
    return json.loads(data["result"]["content"][0]["text"])


def test_http_end_to_end_observes_without_refusing(gdir: Path) -> None:
    junior = registry.issue_token(gdir, "junior", label="t")
    leader = registry.issue_token(gdir, "leader", label="t")
    with TestClient(srv.build_http_app()) as client:
        # junior joue le coup des Blancs : accepté (observation), mais noté
        played = _call(client, "chess_play_move", {"game_id": "g", "move_san": "e4"}, junior)
        assert played["status"] == "ok"
        _call(client, "chess_play_move", {"game_id": "g", "move_san": "e5"}, junior)
        _call(client, "chess_view_state", {"game_id": "g"}, None)
        _call(client, "chess_view_state", {"game_id": "g"}, leader)
    entries = [(e["tool"], e["caller"], e["code"], e["transport"]) for e in _journal(gdir)]
    assert entries == [
        ("chess_play_move", "junior", "NOT_YOUR_TURN", "http"),
        ("chess_play_move", "junior", None, "http"),
        ("chess_view_state", None, "UNAUTHENTICATED", "http"),
        ("chess_view_state", "leader", None, "http"),
    ]
    assert GameState.load("g", games_dir=gdir).san_history == ["e4", "e5"]
