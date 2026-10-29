"""Game creation from a player's token, ``POST /api/games`` (#93, PR 1).

Only unrated games, the creator in one of the two seats, an existing
opponent, a server-chosen id, and no arbiter (Leader, #93, 29/10).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState
from palamede_chessboard.players import registry


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid, kind in (
        ("stephane", "human"),
        ("cilou", "human"),
        ("leader", "agent"),
        ("bot-maia-1500", "agent"),
    ):
        registry.add_player(games_dir, pid, kind=kind)
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    return issued


def _create(client: TestClient, token: str | None, **body):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/games", json={"opponent": "leader", **body}, headers=headers)


def _pgns(games_dir: Path) -> list[Path]:
    return sorted(games_dir.glob("*.pgn"))


@pytest.mark.parametrize(
    ("color", "white", "black"), [("white", "stephane", "leader"), ("black", "leader", "stephane")]
)
def test_creator_gets_the_chosen_side(
    client: TestClient, tokens: dict, games_dir: Path, color: str, white: str, black: str
) -> None:
    r = _create(client, tokens["stephane"], color=color)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["color"] == color
    state = GameState.load(body["game_id"], games_dir=games_dir)
    assert (state.metadata.white, state.metadata.black) == (white, black)
    assert state.metadata.rated is False
    assert state.metadata.arbiter is None
    assert state.metadata.event == "Tablier"
    assert state.board.ply() == 0


def test_random_color_puts_the_creator_in_a_seat(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    for _ in range(5):  # MAX_OPEN_PER_CREATOR
        body = _create(client, tokens["stephane"]).json()
        state = GameState.load(body["game_id"], games_dir=games_dir)
        assert getattr(state.metadata, body["color"]) == "stephane"
        assert {state.metadata.white, state.metadata.black} == {"stephane", "leader"}


def test_game_id_is_chosen_by_the_server(client: TestClient, tokens: dict) -> None:
    r = _create(client, tokens["stephane"], game_id="../../etc/passwd")
    assert re.fullmatch(r"tablier-\d{8}-\d{6}-[0-9a-f]{4}", r.json()["game_id"])


def test_rated_game_is_refused(client: TestClient, tokens: dict, games_dir: Path) -> None:
    """Leader : a rated game keeps the arbiter contract of chess_create_game."""
    r = _create(client, tokens["stephane"], rated=True)
    assert r.status_code == 400
    assert "arbitre" in r.json()["detail"]
    assert _pgns(games_dir) == []


@pytest.mark.parametrize("rated", ["false", 0, None, "true"])
def test_rated_must_be_a_strict_boolean(
    client: TestClient, tokens: dict, games_dir: Path, rated
) -> None:
    r = _create(client, tokens["stephane"], rated=rated)
    assert r.status_code == 400
    assert _pgns(games_dir) == []


def test_bot_opponent_is_unrated(client: TestClient, tokens: dict, games_dir: Path) -> None:
    r = _create(client, tokens["stephane"], opponent="bot-maia-1500", rated=False)
    assert r.status_code == 201
    assert GameState.load(r.json()["game_id"], games_dir=games_dir).metadata.rated is False


@pytest.mark.parametrize("token", [None, "pas-un-jeton"], ids=["absent", "malformé"])
def test_missing_or_invalid_token_is_401(
    client: TestClient, tokens: dict, games_dir: Path, token
) -> None:
    r = _create(client, token)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert _pgns(games_dir) == []


@pytest.mark.parametrize(
    "opponent", ["inconnu", "Stéphane!", "stephane"], ids=["inconnu", "invalide", "soi-même"]
)
def test_bad_opponent_is_422(
    client: TestClient, tokens: dict, games_dir: Path, opponent: str
) -> None:
    r = _create(client, tokens["stephane"], opponent=opponent)
    assert r.status_code == 422
    assert _pgns(games_dir) == []


def test_opponent_name_is_normalised(client: TestClient, tokens: dict, games_dir: Path) -> None:
    r = _create(client, tokens["stephane"], opponent="Leader", color="white")
    assert GameState.load(r.json()["game_id"], games_dir=games_dir).metadata.black == "leader"


def test_time_control_starts_a_clock(client: TestClient, tokens: dict) -> None:
    r = _create(client, tokens["stephane"], time_control={"format": "5+3"})
    assert r.json()["clock"] is not None


def test_no_clock_by_default(client: TestClient, tokens: dict) -> None:
    assert _create(client, tokens["stephane"]).json()["clock"] is None


def test_token_never_in_the_answer(client: TestClient, tokens: dict) -> None:
    r = _create(client, tokens["stephane"])
    assert tokens["stephane"] not in r.text


def test_unreadable_registry_is_503(client: TestClient, tokens: dict, games_dir: Path) -> None:
    registry.registry_path(games_dir).write_text("{pas du json")
    r = _create(client, tokens["stephane"])
    assert r.status_code == 503
    assert _pgns(games_dir) == []


def test_new_game_can_be_played_by_its_creator(client: TestClient, tokens: dict) -> None:
    """The created game is an ordinary one : the creator's token plays in it."""
    game_id = _create(client, tokens["stephane"], color="white").json()["game_id"]
    r = client.post(
        f"/api/games/{game_id}/move",
        json={"san": "e4"},
        headers={"Authorization": f"Bearer {tokens['stephane']}"},
    )
    assert r.status_code == 200, r.text


# Review Leader and Junior (#126) : the PGN writer does not escape header
# values, so the time control is whitelisted before anything is written.
FORGED = [
    "٥+٣",
    '5+3"]\n[Rated "true',
    '5+3"]\n[Result "1-0',
    "5+3\n",
    "5 + 3",
    "blitz",
    "",
    "0+5",
    "181+0",
    "5+181",
    "-1+0",
    "1234+0",
]


@pytest.mark.parametrize("fmt", FORGED)
def test_time_control_outside_the_whitelist_is_422(
    client: TestClient, tokens: dict, games_dir: Path, fmt: str
) -> None:
    r = _create(client, tokens["stephane"], time_control={"format": fmt})
    assert r.status_code == 422
    assert _pgns(games_dir) == []


@pytest.mark.parametrize("extra", [{"start_seconds": -60}, {"increment_seconds": 5}])
def test_time_control_takes_no_other_field(
    client: TestClient, tokens: dict, games_dir: Path, extra: dict
) -> None:
    r = _create(client, tokens["stephane"], time_control={"format": "5+3", **extra})
    assert r.status_code == 422
    assert _pgns(games_dir) == []


@pytest.mark.parametrize(("fmt", "seconds"), [("1+0", 60), ("15+10", 900), ("180+180", 10800)])
def test_bounded_time_control_is_kept(
    client: TestClient, tokens: dict, games_dir: Path, fmt: str, seconds: int
) -> None:
    body = _create(client, tokens["stephane"], time_control={"format": fmt}).json()
    assert body["clock"]["white_seconds"] == seconds
    state = GameState.load(body["game_id"], games_dir=games_dir)
    assert state.metadata.time_control.format == fmt
    assert state.metadata.rated is False


def test_unauthenticated_caller_learns_nothing_about_rated(
    client: TestClient, tokens: dict
) -> None:
    """Review Leader : the token is checked first, before any other answer."""
    assert _create(client, None, rated=True).status_code == 401
    assert _create(client, None, time_control={"format": "x"}).status_code == 401


def test_human_opponent_is_refused(client: TestClient, tokens: dict, games_dir: Path) -> None:
    """Owner, 29/10 : never a human put in a game without their consent."""
    r = _create(client, tokens["leader"], opponent="cilou")
    assert r.status_code == 422
    assert "humain" in r.json()["detail"]
    assert _pgns(games_dir) == []


def test_a_human_may_open_a_game_against_an_agent(client: TestClient, tokens: dict) -> None:
    assert _create(client, tokens["cilou"], opponent="leader").status_code == 201


def test_at_most_five_open_games_per_creator(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    for _ in range(5):
        assert _create(client, tokens["stephane"]).status_code == 201
    r = _create(client, tokens["stephane"], opponent="bot-maia-1500")
    assert r.status_code == 429
    assert len(_pgns(games_dir)) == 5
    # Another creator has their own allowance.
    assert _create(client, tokens["cilou"]).status_code == 201


def test_finished_and_other_games_do_not_count(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    ids = [_create(client, tokens["stephane"]).json()["game_id"] for _ in range(5)]
    state = GameState.load(ids[0], games_dir=games_dir)
    state.metadata.result = "1-0"
    state.save()
    GameState.create(
        GameMetadata(game_id="hors-tablier", white="stephane", black="leader"), games_dir=games_dir
    )
    assert _create(client, tokens["stephane"]).status_code == 201


def test_parallel_requests_never_exceed_the_limit(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    """Review Leader #126 : 12 parallel POSTs for one creator leave exactly 5 games."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(lambda _: _create(client, tokens["stephane"]).status_code, range(12)))
    assert sorted(codes) == [201] * 5 + [429] * 7
    assert len(_pgns(games_dir)) == 5


def test_opponent_of_an_unknown_kind_is_refused(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    """Fail-closed : only an agent account may be put in a game by someone else."""
    raw = registry.load(games_dir)
    raw["players"]["leader"]["kind"] = "robot"
    registry.registry_path(games_dir).write_text(json.dumps(raw))
    assert _create(client, tokens["stephane"]).status_code == 422
