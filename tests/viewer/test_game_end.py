"""Ending a game from the viewer (#275) : draw routes, the stream, the page."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.players import registry
from palamede_chessboard.viewer.models import TEMPLATES_DIR, _game_stream_payload

GAME_END_JS = (TEMPLATES_DIR / "viewer" / "_game_end.js.jinja").read_text(encoding="utf-8")
GAME = "viewer-draw"


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid in ("alice", "bob", "carol"):
        registry.add_player(games_dir, pid, kind="human")
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    GameState.create(
        GameMetadata(
            game_id=GAME, white="alice", black="bob", time_control=TimeControl(format="unrated")
        ),
        games_dir=games_dir,
    )
    return issued


def _post(client: TestClient, action: str, token: str | None, game: str = GAME):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(f"/api/games/{game}/draw/{action}", headers=headers)


def _state(games_dir: Path) -> GameState:
    return GameState.load(GAME, games_dir=games_dir)


def test_an_offer_then_an_acceptance_ends_the_game(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    offered = _post(client, "offer", tokens["alice"])
    assert offered.status_code == 200, offered.text
    assert _state(games_dir).metadata.draw_offer["by"] == "white"
    accepted = _post(client, "accept", tokens["bob"])
    assert accepted.status_code == 200, accepted.text
    state = _state(games_dir)
    assert state.metadata.result == "1/2-1/2" and state.metadata.draw_offer is None


def test_the_answer_says_ok_and_returns_the_state(client: TestClient, tokens: dict) -> None:
    body = _post(client, "offer", tokens["alice"]).json()
    assert body["status"] == "ok" and body["state"]["draw_offer"]["by"] == "white"


def test_holding_both_seats_the_offer_is_from_the_side_to_move(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    GameState.create(
        GameMetadata(
            game_id="both-seats",
            white="alice",
            black="alice",
            time_control=TimeControl(format="unrated"),
        ),
        games_dir=games_dir,
    )
    state = GameState.load("both-seats", games_dir=games_dir)
    state.play_move("e4")
    assert _post(client, "offer", tokens["alice"], game="both-seats").status_code == 200
    reloaded = GameState.load("both-seats", games_dir=games_dir)
    assert reloaded.metadata.draw_offer["by"] == "black"


def test_a_refusal_clears_the_offer_and_the_game_goes_on(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    _post(client, "offer", tokens["alice"])
    assert _post(client, "decline", tokens["bob"]).status_code == 200
    state = _state(games_dir)
    assert state.metadata.draw_offer is None and state.metadata.result == "*"


def test_an_offer_made_by_mcp_is_answered_from_the_viewer(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    _state(games_dir).offer_draw(by="black")  # the state machine of the MCP tools
    assert _post(client, "accept", tokens["alice"]).status_code == 200
    assert _state(games_dir).metadata.result == "1/2-1/2"


def test_an_agreed_draw_stops_the_clock(
    client: TestClient, games_dir: Path, tokens: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from palamede_chessboard.viewer.routers import games

    stopped: list[str] = []
    real = games.stop_clock_locked
    monkeypatch.setattr(
        games, "stop_clock_locked", lambda game, gdir: (stopped.append(game), real(game, gdir))
    )
    _post(client, "offer", tokens["alice"])
    assert stopped == []
    _post(client, "accept", tokens["bob"])
    assert stopped == [GAME]


@pytest.mark.parametrize(
    ("action", "who", "status"),
    [
        ("offer", None, 401),
        ("accept", None, 401),
        ("decline", None, 401),
        ("offer", "carol", 403),
        ("accept", "carol", 403),
        ("decline", "carol", 403),
    ],
)
def test_only_a_player_of_the_game_may_act(
    client: TestClient, tokens: dict, action: str, who: str | None, status: int
) -> None:
    assert _post(client, action, tokens[who] if who else None).status_code == status


@pytest.mark.parametrize("action", ["offer", "accept", "decline"])
def test_a_missing_game_is_404(client: TestClient, tokens: dict, action: str) -> None:
    assert _post(client, action, tokens["alice"], game="ghost-game").status_code == 404


@pytest.mark.parametrize(
    ("first", "action", "who"),
    [
        (None, "accept", "bob"),  # nothing to accept
        (None, "decline", "bob"),
        ("alice", "accept", "alice"),  # not one's own offer
        ("alice", "decline", "alice"),
        ("alice", "offer", "bob"),  # the other side's offer stands
    ],
)
def test_what_the_state_machine_refuses_is_409(
    client: TestClient, tokens: dict, first: str | None, action: str, who: str
) -> None:
    if first:
        _post(client, "offer", tokens[first])
    assert _post(client, action, tokens[who]).status_code == 409


def test_no_draw_on_a_finished_game(client: TestClient, games_dir: Path, tokens: dict) -> None:
    _state(games_dir).resign(by="white")
    assert _post(client, "offer", tokens["bob"]).status_code == 409


def test_the_stream_carries_the_offer(games_dir: Path, tokens: dict) -> None:
    assert _game_stream_payload(_state(games_dir))["draw_offer"] is None
    _state(games_dir).offer_draw(by="white")
    assert _game_stream_payload(_state(games_dir))["draw_offer"]["by"] == "white"


def test_the_band_is_on_the_page(client: TestClient) -> None:
    assert '<div class="game-end" id="game-end" hidden></div>' in client.get("/").text


def test_the_script_loads_before_the_stream_starts() -> None:
    # Its const is read when the stream starts, at the end of _live.
    bundle = (TEMPLATES_DIR / "viewer.js.jinja").read_text(encoding="utf-8")
    assert bundle.index("_game_end.js.jinja") < bundle.index("_live.js.jinja")


def test_the_page_shows_the_offer_from_the_stream() -> None:
    live = (TEMPLATES_DIR / "viewer" / "_live.js.jinja").read_text(encoding="utf-8")
    assert "updateGameEnd(data);" in live
    assert "refreshGameEnd();" in live


def test_the_buttons_follow_the_offer() -> None:
    assert "offer && !colors.includes(offer.by)" in GAME_END_JS
    for action in ("draw/offer", "draw/accept", "draw/decline", "resign"):
        assert f'data-end="{action}"' in GAME_END_JS


def test_resigning_asks_for_a_confirmation() -> None:
    assert "button.dataset.end === 'resign' && !window.confirm(" in GAME_END_JS


def test_the_buttons_are_for_the_players_of_a_live_game() -> None:
    assert "gameEndBox.hidden = !colors.length || !live;" in GAME_END_JS
    assert "gameId().startsWith('exo.')" in GAME_END_JS
