"""One operation, the same refusals, by MCP and by HTTP (#332, point 3).

S12 (cases 6 to 8) : the viewer's HTTP API refused in French (#326, #327),
the MCP tools in English, from the domain's exception text. The domain now
raises the viewer's texts (:mod:`palamede_chessboard.refusals`) and the MCP
``reason`` is the HTTP ``detail``, word for word ; the ``code`` is unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard import refusals
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.players import registry
from palamede_chessboard.viewer import refusals as viewer_refusals
from tests.mcp.conftest import _decode

GAME = "agents-332"
#: The MCP verb → the HTTP route (``/draw/accept`` for ``agree_draw``).
_HTTP_VERB = {"offer": "offer", "agree": "accept", "decline": "decline"}


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid, kind in (("stephane", "human"), ("bot-maia-1100", "agent"), ("patrice", "human")):
        registry.add_player(games_dir, pid, kind=kind)
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    return issued


def _game(games_dir: Path, *, result: str = "*", offer_by: str | None = None) -> GameState:
    """An agents' game without clock : stephane (white) vs bot-maia-1100, 1.e4 played."""
    state = GameState.create(
        GameMetadata(
            game_id=GAME,
            white="stephane",
            black="bot-maia-1100",
            time_control=TimeControl(format="unrated"),
        ),
        games_dir=games_dir,
    )
    state.play_move("e4")
    if offer_by is not None:
        state.offer_draw(by=offer_by)
    if result != "*":
        state.metadata.result, state.metadata.termination = result, "normal"
        state.save()
    return state


def _http(client: TestClient, token: str, path: str, **payload):
    return client.post(
        f"/api/games/{GAME}/{path}",
        json=payload or None,
        headers={"Authorization": f"Bearer {token}"},
    )


async def _mcp(tool: str, **arguments):
    return _decode(await call_tool(tool, {"game_id": GAME, **arguments}))


# --- the viewer's texts and the domain's are the same strings ---------------------------------


def test_the_domain_and_the_viewer_carry_the_same_texts() -> None:
    """Until ``viewer/refusals.py`` imports from the domain (Leader's lot), two copies, kept equal."""
    assert refusals.GAME_OVER == viewer_refusals.GAME_OVER
    assert refusals.UNREADABLE == viewer_refusals.UNREADABLE
    assert refusals.NO_MOVE == viewer_refusals.NO_MOVE
    assert refusals.french_san("Rf1 exd8=Q+ O-O") == viewer_refusals.french_san("Rf1 exd8=Q+ O-O")
    assert refusals.not_possible("Tf1") == viewer_refusals.not_possible("Tf1")
    assert refusals.not_possible("aucune pièce en h1") == viewer_refusals.not_possible(
        "aucune pièce en h1"
    )


@pytest.mark.parametrize(
    ("text", "action", "offer", "side"),
    [
        (refusals.END_GAME_OVER, "resign", None, "white"),
        (refusals.END_OPPONENT_OFFERED, "offer", {"by": "black", "ply": 1}, "white"),
        (refusals.END_NO_OFFER, "agree", None, "white"),
        (refusals.END_OWN_OFFER, "agree", {"by": "white", "ply": 1}, "white"),
    ],
)
def test_the_end_texts_are_those_of_the_viewer(
    text: str, action: str, offer: dict | None, side: str
) -> None:
    import chess

    board = chess.Board()
    result = "1-0" if text == refusals.END_GAME_OVER else "*"
    assert text == viewer_refusals.end_refused(action, board, result, offer, side)


def test_the_identity_texts_are_those_of_the_viewer() -> None:
    from palamede_chessboard.viewer.routers import games

    assert refusals.MOVE_REFUSALS == games._MOVE_REFUSALS


# --- MCP reason == HTTP detail, case by case ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_move_on_a_game_over_is_refused_the_same_way(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    _game(games_dir, result="1-0")
    by_http = _http(client, tokens["bot-maia-1100"], "move", san="Nc6")
    by_mcp = await _mcp("chess_play_move", move_san="Nc6")
    assert by_http.status_code == 409
    assert (by_mcp["status"], by_mcp["code"]) == ("error", "INVALID_STATE")
    assert by_mcp["reason"] == by_http.json()["detail"] == "Coup refusé : la partie est terminée."
    assert GameState.load(GAME, games_dir=games_dir).san_history == ["e4"]


@pytest.mark.asyncio
async def test_an_illegal_move_is_refused_the_same_way_in_french_notation(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    _game(games_dir)
    by_http = _http(client, tokens["bot-maia-1100"], "move", san="Rf1")
    by_mcp = await _mcp("chess_play_move", move_san="Rf1")
    assert by_http.status_code == 422
    assert (by_mcp["status"], by_mcp["code"]) == ("error", "ILLEGAL_MOVE")
    assert (
        by_mcp["reason"]
        == by_http.json()["detail"]
        == "Coup refusé : Tf1 n'est pas possible dans cette position."
    )
    # The engine-guard's reason stays out of the text (no ply, no English).
    assert "ply" not in by_mcp["reason"] and "rejected" not in by_mcp["reason"]


@pytest.mark.asyncio
async def test_a_move_that_names_no_piece_is_said_the_french_way(games_dir: Path) -> None:
    _game(games_dir)
    body = await _mcp("chess_play_move", move_san="Zz9")
    assert body["code"] == "ILLEGAL_MOVE"
    assert body["reason"] == refusals.not_possible("Zz9")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["resign", "offer_draw", "agree_draw", "decline_draw"])
async def test_an_end_action_on_a_game_over_is_refused_the_same_way(
    client: TestClient, tokens: dict, games_dir: Path, action: str
) -> None:
    _game(games_dir, result="1-0")
    path = "resign" if action == "resign" else "draw/" + _HTTP_VERB[action.split("_")[0]]
    by_http = _http(client, tokens["stephane"], path)
    by_mcp = await _mcp(f"chess_{action}", by="white")
    assert by_http.status_code == 409, by_http.json()
    assert (by_mcp["status"], by_mcp["code"]) == ("error", "INVALID_STATE")
    assert by_mcp["reason"] == by_http.json()["detail"] == "La partie est terminée."


@pytest.mark.asyncio
async def test_offering_when_the_opponent_already_did_is_refused_the_same_way(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    _game(games_dir, offer_by="black")
    by_http = _http(client, tokens["stephane"], "draw/offer")
    by_mcp = await _mcp("chess_offer_draw", by="white")
    assert by_http.status_code == 409
    assert by_mcp["code"] == "INVALID_STATE"
    assert by_mcp["reason"] == by_http.json()["detail"] == refusals.END_OPPONENT_OFFERED


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["agree", "decline"])
async def test_answering_no_offer_is_refused_the_same_way(
    client: TestClient, tokens: dict, games_dir: Path, action: str
) -> None:
    _game(games_dir)
    by_http = _http(client, tokens["stephane"], "draw/" + _HTTP_VERB[action])
    by_mcp = await _mcp(f"chess_{action}_draw", by="white")
    assert by_http.status_code == 409
    assert by_mcp["code"] == "INVALID_STATE"
    assert by_mcp["reason"] == by_http.json()["detail"] == refusals.END_NO_OFFER


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["agree", "decline"])
async def test_answering_ones_own_offer_is_refused_the_same_way(
    client: TestClient, tokens: dict, games_dir: Path, action: str
) -> None:
    _game(games_dir, offer_by="white")
    by_http = _http(client, tokens["stephane"], "draw/" + _HTTP_VERB[action])
    by_mcp = await _mcp(f"chess_{action}_draw", by="white")
    assert by_http.status_code == 409
    assert by_mcp["code"] == "INVALID_STATE"
    assert by_mcp["reason"] == by_http.json()["detail"] == refusals.END_OWN_OFFER


# --- identity refusals of chess_seat : the code, now with the viewer's text --------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("who", "code", "status"),
    [(None, "UNAUTHENTICATED", 401), ("patrice", "NOT_A_PLAYER", 403)],
)
async def test_chess_seat_refuses_with_the_viewers_text(
    client: TestClient,
    tokens: dict,
    games_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    who: str | None,
    code: str,
    status: int,
) -> None:
    from palamede_chessboard import mcp_chessboard_server

    _game(games_dir)
    headers = {"authorization": f"Bearer {tokens[who]}"} if who else {}
    monkeypatch.setattr(mcp_chessboard_server, "_request_headers", lambda: headers)
    by_mcp = await _mcp("chess_seat")
    by_http = client.get(
        f"/api/games/{GAME}/seat",
        headers={"Authorization": f"Bearer {tokens[who]}"} if who else {},
    )
    assert by_http.status_code == status
    assert (by_mcp["status"], by_mcp["code"]) == ("error", code)
    assert by_mcp["reason"] == by_http.json()["detail"]
