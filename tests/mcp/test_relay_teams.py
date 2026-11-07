"""Relay teams declared per game (#280) : ``WhiteTeam`` / ``BlackTeam``.

The team is set at creation, its members are registry accounts, humans and
agents mixed ; each member plays with their own token and ``by`` stays theirs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard import mcp_chessboard_server
from palamede_chessboard.chessboard_state import GameMetadata, GameState
from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.players import observe as obs
from palamede_chessboard.players import registry
from palamede_chessboard.viewer import build_app
from tests.mcp.conftest import _decode

TEAM = "relais-leader-junior-cd"
SIX = ["leader", "admin", "junior", "cd", "stephane", "patrice"]
HUMANS = {"stephane", "patrice"}


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid in SIX + ["maia"]:
        registry.add_player(games_dir, pid, kind="human" if pid in HUMANS else "agent")
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    return issued


@pytest.fixture
def client(games_dir: Path) -> TestClient:
    return TestClient(build_app())


async def _call(name: str, arguments: dict) -> dict:
    return _decode(await call_tool(name, arguments))


async def _relay(game_id: str = "relais", **extra) -> dict:
    """Patrice against a mixed team (an agent and the owner, among others)."""
    arguments = {
        "game_id": game_id,
        "white": "patrice",
        "black": TEAM,
        "black_team": ["Leader", "junior", "cd", "stephane"],
        "arbiter": "admin",
        **extra,
    }
    return await _call("chess_create_game", arguments)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---- création -------------------------------------------------------------------


async def test_team_is_written_normalised_and_survives_load_save(tokens, games_dir) -> None:
    created = await _relay()
    assert created["status"] == "ok", created
    state = GameState.load("relais", games_dir=games_dir)
    assert state.metadata.black_team == ["leader", "junior", "cd", "stephane"]
    assert state.metadata.white_team == []
    text = (games_dir / "relais.pgn").read_text(encoding="utf-8")
    assert '[BlackTeam "leader,junior,cd,stephane"]' in text
    assert "WhiteTeam" not in text
    state.play_move("e4", by="patrice")
    state.save()
    again = GameState.load("relais", games_dir=games_dir)
    assert again.metadata.black_team == ["leader", "junior", "cd", "stephane"]
    assert again.san_history == ["e4"]


async def test_two_teams_of_six_members_and_one(tokens, client: TestClient) -> None:
    created = await _call(
        "chess_create_game",
        {
            "game_id": "six",
            "white": "relais-six",
            "white_team": SIX,
            "black": "relais-maia",
            "black_team": ["maia"],
            "arbiter": "admin",
        },
    )
    assert created["status"] == "ok", created
    for pid in SIX:
        r = client.get("/api/games/six/seat", headers=_bearer(tokens[pid]))
        assert r.json()["colors"] == ["white"], pid
    r = client.get("/api/games/six/seat", headers=_bearer(tokens["maia"]))
    assert r.json()["colors"] == ["black"]


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ({"black_team": ["leader", "inconnu"]}, "unknown player 'inconnu'"),
        (  # une équipe blanche seule est validée aussi
            {"white": "relais-b", "white_team": ["inconnu"], "black": "leader", "black_team": []},
            "unknown player 'inconnu'",
        ),
        ({"black_team": ["../evil"]}, "invalid player id"),
        ({"black_team": "leader,junior"}, "must be a list"),
        ({"black_team": ["leader", 7]}, "must be a list"),
        ({"black_team": ["leader", "Leader"]}, "twice"),
        ({"black_team": ["patrice", "leader"]}, "both sides"),  # face à lui-même
        ({"black_team": ["leader"], "white": "relais-b", "white_team": ["leader"]}, "both sides"),
        ({"black_team": [TEAM, "leader"]}, "is a team, not a player"),  # l'équipe dans l'équipe
        ({"black_team": ["leader"], "white": "relais-b", "white_team": [TEAM]}, "both sides"),
    ],
)
async def test_bad_teams_create_nothing(tokens, games_dir, extra: dict, error: str) -> None:
    answer = await _relay(**extra)
    assert answer["status"] == "error" and error in answer["reason"], answer
    assert not (games_dir / "relais.pgn").exists()


async def test_unreadable_registry_creates_nothing(tokens, games_dir, monkeypatch) -> None:
    def broken(*_args, **_kwargs):
        raise registry.RegistryError("illisible")

    monkeypatch.setattr(registry, "load", broken)
    answer = await _relay()
    assert answer["status"] == "error" and "registry unreadable" in answer["reason"]
    assert not (games_dir / "relais.pgn").exists()


async def test_no_tool_changes_a_team_after_creation(tokens) -> None:
    """A second create of the same id is refused : the team stays as written."""
    await _relay()
    again = await _relay(black_team=["admin"], arbiter="cd")
    assert again["status"] == "error" and again["code"] == "GAME_ALREADY_EXISTS"


@pytest.mark.parametrize(
    ("who", "expected"),
    [
        ("junior", None),  # membre
        ("admin", None),  # arbitre
        ("patrice", None),  # joueur
        ("maia", "NOT_A_PLAYER"),  # ni membre ni arbitre
    ],
)
def test_creator_rule_counts_the_members(who: str, expected) -> None:
    arguments = {"white": "patrice", "black": TEAM, "black_team": ["junior"], "arbiter": "admin"}
    assert obs.evaluate("chess_create_game", arguments, who, None) == expected


@pytest.mark.parametrize("who", ["junior", "j"])
def test_creator_rule_ignores_a_malformed_team(who: str) -> None:
    """A string is not read letter by letter as a list of members."""
    arguments = {"white": "patrice", "black": TEAM, "black_team": "junior"}
    assert obs.evaluate("chess_create_game", arguments, who, None) == "NOT_A_PLAYER"


# ---- jouer en équipe -------------------------------------------------------------


async def test_mcp_seat_of_members_and_outsiders(tokens, monkeypatch) -> None:
    await _relay()
    for pid, expected in (("junior", ["black"]), ("stephane", ["black"]), ("patrice", ["white"])):
        headers = {"authorization": f"Bearer {tokens[pid]}"}
        monkeypatch.setattr(mcp_chessboard_server, "_request_headers", lambda h=headers: h)
        answer = await _call("chess_seat", {"game_id": "relais"})
        assert answer == {"status": "ok", "game_id": "relais", "player": pid, "colors": expected}
    monkeypatch.setattr(
        mcp_chessboard_server,
        "_request_headers",
        lambda: {"authorization": f"Bearer {tokens['maia']}"},
    )
    answer = await _call("chess_seat", {"game_id": "relais"})
    assert answer["code"] == "NOT_A_PLAYER"


async def test_an_agent_and_a_human_member_each_play_with_their_token(
    tokens, client: TestClient, games_dir: Path
) -> None:
    await _relay()
    assert (
        client.post(
            "/api/games/relais/move", json={"san": "e4"}, headers=_bearer(tokens["patrice"])
        ).status_code
        == 200
    )
    r = client.post("/api/games/relais/move", json={"san": "e5"}, headers=_bearer(tokens["junior"]))
    assert r.status_code == 200 and r.json()["move"]["by"] == "junior"
    assert (
        client.post(
            "/api/games/relais/move", json={"san": "Nf3"}, headers=_bearer(tokens["patrice"])
        ).status_code
        == 200
    )
    r = client.post(
        "/api/games/relais/move", json={"san": "Nc6"}, headers=_bearer(tokens["stephane"])
    )
    assert r.status_code == 200 and r.json()["move"]["by"] == "stephane"
    assert GameState.load("relais", games_dir=games_dir).san_history == ["e4", "e5", "Nf3", "Nc6"]


async def test_member_out_of_turn_and_outsider_are_refused(
    tokens, client: TestClient, games_dir: Path
) -> None:
    await _relay()
    r = client.post("/api/games/relais/move", json={"san": "e4"}, headers=_bearer(tokens["junior"]))
    assert r.status_code == 403 and "NOT_YOUR_TURN" in r.json()["detail"]
    r = client.post("/api/games/relais/move", json={"san": "e4"}, headers=_bearer(tokens["admin"]))
    assert r.status_code == 403 and "NOT_A_PLAYER" in r.json()["detail"]  # l'arbitre ne joue pas
    assert GameState.load("relais", games_dir=games_dir).san_history == []


async def test_viewer_seat_and_resign_of_a_member(
    tokens, client: TestClient, games_dir: Path
) -> None:
    await _relay()
    r = client.get("/api/games/relais/seat", headers=_bearer(tokens["cd"]))
    assert r.json() == {"game_id": "relais", "player": "cd", "colors": ["black"]}
    assert client.get("/api/games/relais/seat", headers=_bearer(tokens["maia"])).status_code == 403
    r = client.post("/api/games/relais/resign", headers=_bearer(tokens["cd"]))
    assert r.status_code == 200
    assert GameState.load("relais", games_dir=games_dir).metadata.result == "1-0"


def test_games_without_a_team_are_unchanged(tokens, games_dir: Path) -> None:
    GameState.create(
        GameMetadata(game_id="duel", white="patrice", black="leader"), games_dir=games_dir
    )
    roles = obs.roles_of(GameState.load("duel", games_dir=games_dir))
    assert roles["seats"] == {"white": {"patrice"}, "black": {"leader"}}
    assert (roles["players"], roles["to_move"]) == ({"patrice", "leader"}, {"patrice"})
    assert "Team" not in (games_dir / "duel.pgn").read_text(encoding="utf-8")


def test_by_black_matches_a_member(tokens, games_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv(obs.AUTH_MODE_ENV, "observe")
    GameState.create(
        GameMetadata(game_id="r", white="patrice", black=TEAM, black_team=["junior", "cd"]),
        games_dir=games_dir,
    )
    headers = {"authorization": f"Bearer {tokens['junior']}"}
    for by, matches in (("black", True), ("junior", True), ("cd", False)):
        entry = obs.observe(
            "chess_resign", {"game_id": "r", "by": by}, headers, games_dir, transport="http"
        )
        assert entry is not None and entry["by_matches"] is matches, by
