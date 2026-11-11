"""Lessons (``cours.*``, #323, step 1) : one operation, the same by MCP and by HTTP.

The owner's rule (11/11, #328) : creating a lesson does not depend on the
interface. ``chess_create_lesson`` and ``POST /api/lessons`` both call
``create_lesson`` ; the parity tests below hold them to the same result and
the same refusals.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState
from palamede_chessboard.chessboard_viewer import build_app
from palamede_chessboard.cours import LessonError, create_lesson
from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.players import registry

ID_RE = r"cours\.italienne\.stephane-cd\.\d{8}-(\d{2})"


@pytest.fixture
def games_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    monkeypatch.setenv("OUTILLAGES_ENGINE_CALLS_LOG", str(tmp_path / "engine.jsonl"))
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    # A live arbiter daemon : chess_create_lesson then needs no ``arbiter``.
    (tmp_path / ".arbiter-daemon.pid").write_text(str(os.getpid()), encoding="utf-8")
    for pid, kind in (("stephane", "human"), ("cd", "agent"), ("cilou", "human")):
        registry.add_player(tmp_path, pid, kind=kind)
    return tmp_path


@pytest.fixture
def tokens(games_dir: Path) -> dict[str, str]:
    return {p: registry.issue_token(games_dir, p, label="tests") for p in ("stephane", "cilou")}


@pytest.fixture
def client(games_dir: Path) -> TestClient:
    return TestClient(build_app())


async def _mcp(**arguments) -> dict:
    result = await call_tool("chess_create_lesson", {"arbiter": "leader", **arguments})
    body = json.loads(result[0].text)
    body.pop("_metadata", None)
    return body


def _http(client: TestClient, token: str | None, **body):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/lessons", json=body, headers=headers)


LESSON = {"theme": "italienne", "white": "stephane", "black": "cd"}


def test_a_lesson_is_unrated_without_clock_and_says_its_mode(games_dir: Path) -> None:
    state = create_lesson(games_dir, **LESSON)
    assert re.fullmatch(ID_RE, state.metadata.game_id)
    pgn = state.raw_pgn()
    assert '[Mode "cours"]' in pgn
    assert '[TimeControl "-"]' in pgn
    assert "[Rated " not in pgn
    assert not list(games_dir.glob("*.clock.json"))
    again = GameState.load(state.metadata.game_id, games_dir=games_dir)
    assert again.metadata.mode == "cours"


def test_numbers_follow_each_other_within_a_day(games_dir: Path) -> None:
    ids = [create_lesson(games_dir, **LESSON).metadata.game_id for _ in range(3)]
    assert [re.fullmatch(ID_RE, i).group(1) for i in ids] == ["01", "02", "03"]


def test_names_are_normalised(games_dir: Path) -> None:
    state = create_lesson(games_dir, theme="italienne", white="Stephane", black=" CD ")
    assert (state.metadata.white, state.metadata.black) == ("stephane", "cd")


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ({**LESSON, "theme": "Italienne!"}, "Thème invalide"),
        ({**LESSON, "theme": ""}, "Thème invalide"),
        ({**LESSON, "theme": None}, "Thème invalide"),
        ({**LESSON, "black": "inconnu"}, "Joueur des noirs inconnu"),
        ({**LESSON, "white": "a b"}, "Joueur des blancs invalide"),
        ({**LESSON, "black": "stephane"}, "Un cours se joue à deux"),
        (
            {"theme": "t" * 24, "white": "a" * 32, "black": "cd"},
            "Identifiant du cours trop long",
        ),
    ],
)
def test_refusals(games_dir: Path, arguments: dict, reason: str) -> None:
    registry.add_player(games_dir, "a" * 32, kind="human")
    with pytest.raises(LessonError, match=reason):
        create_lesson(games_dir, **arguments)
    assert not list(games_dir.glob("cours.*.pgn"))


@pytest.mark.asyncio
async def test_chess_create_game_refuses_a_cours_id(games_dir: Path) -> None:
    result = await call_tool(
        "chess_create_game",
        {"game_id": "cours.x.stephane-cd.20261111-01", "white": "stephane", "black": "cd"},
    )
    body = json.loads(result[0].text)
    assert body["status"] == "error"
    assert "reserved" in body["reason"]


def test_the_lessons_of_10_11_stay_readable(games_dir: Path) -> None:
    """Written by hand before the prefix was reserved, without ``Mode`` (#323)."""
    game_id = "cours.italienne.stephane-cd.20261110-01"
    GameState.create(
        GameMetadata(game_id=game_id, white="stephane", black="cd"), games_dir=games_dir
    )
    state = GameState.load(game_id, games_dir=games_dir)
    assert state.metadata.mode is None
    assert "[Mode " not in state.raw_pgn()


@pytest.mark.asyncio
async def test_mcp_creates_the_same_lesson(games_dir: Path) -> None:
    body = await _mcp(**LESSON)
    assert body["status"] == "ok", body
    assert re.fullmatch(ID_RE, body["game_id"])
    assert GameState.load(body["game_id"], games_dir=games_dir).metadata.arbiter == "leader"


def test_http_creates_the_same_lesson(client: TestClient, tokens: dict, games_dir: Path) -> None:
    r = _http(client, tokens["stephane"], **LESSON)
    assert r.status_code == 201, r.text
    state = GameState.load(r.json()["game_id"], games_dir=games_dir)
    assert (state.metadata.mode, state.metadata.white, state.metadata.black) == (
        "cours",
        "stephane",
        "cd",
    )


@pytest.mark.asyncio
async def test_mcp_and_http_give_the_same_pgn(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    """Parity : same headers and same movetext, the number apart."""
    by_mcp = (await _mcp(arbiter="", **LESSON))["game_id"]
    by_http = _http(client, tokens["stephane"], **LESSON).json()["game_id"]
    pgn_mcp = GameState.load(by_mcp, games_dir=games_dir).raw_pgn()
    pgn_http = GameState.load(by_http, games_dir=games_dir).raw_pgn()
    assert pgn_mcp == pgn_http


@pytest.mark.parametrize(
    "arguments",
    [
        {**LESSON, "theme": "Italienne!"},
        {**LESSON, "black": "inconnu"},
        {**LESSON, "black": "stephane"},
    ],
)
@pytest.mark.asyncio
async def test_mcp_and_http_give_the_same_refusal(
    client: TestClient, tokens: dict, arguments: dict
) -> None:
    by_mcp = await _mcp(**arguments)
    by_http = _http(client, tokens["stephane"], **arguments)
    assert by_mcp["status"] == "error"
    assert by_http.status_code == 422
    assert by_mcp["reason"] == by_http.json()["detail"]


def test_http_needs_a_token(client: TestClient) -> None:
    assert _http(client, None, **LESSON).status_code == 401


def test_http_refuses_a_lesson_the_token_does_not_sit_in(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    r = _http(client, tokens["cilou"], **LESSON)
    assert r.status_code == 403
    assert not list(games_dir.glob("cours.*.pgn"))


def test_http_refuses_extra_fields(client: TestClient, tokens: dict) -> None:
    r = _http(client, tokens["stephane"], game_id="cours.x", **LESSON)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_mcp_keeps_the_arbiter_gate_of_chess_create_game(games_dir: Path) -> None:
    (games_dir / ".arbiter-daemon.pid").unlink()
    body = await _mcp(**{**LESSON, "arbiter": ""})
    assert body["status"] == "error"
    assert "Arbiter required" in body["reason"]
    assert not list(games_dir.glob("cours.*.pgn"))


def test_http_seat_is_compared_on_normalised_names(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    """« Stephane » in the body is the token's « stephane » (review Junior #329)."""
    r = _http(client, tokens["stephane"], theme="italienne", white="Stephane", black="cd")
    assert r.status_code == 201, r.text
