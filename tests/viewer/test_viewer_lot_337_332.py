"""Viewer lot of 11/11 : #337 and #332 point 1, and the domain's refusals (#340).

* #337 : at each game change the role is read again from the token's seat,
  and the last move highlighted is the new game's own, never the previous one.
* #332 point 1 : a drop the board used to block locally (game over, a piece of
  the side not to move) now reaches the server, which says why in French.
* #340 : the viewer's refusals are the domain's texts ; a SAN move refused for
  its notation reads the same in the viewer as through the MCP.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard import refusals
from palamede_chessboard.chessboard_state import GameState, IllegalMoveError
from palamede_chessboard.coach import play
from palamede_chessboard.coach.store import add_exercises
from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.players import registry
from palamede_chessboard.viewer import refusals as viewer_refusals
from palamede_chessboard.viewer.models import TEMPLATES_DIR
from palamede_chessboard.viewer.routers import games as games_router
from tests.coach.test_exercise_guard import ATTACKED, EXERCISE, HUMAN
from tests.mcp.conftest import _decode


def _js(name: str) -> str:
    return (TEMPLATES_DIR / "viewer" / name).read_text(encoding="utf-8")


def _function_body(js: str, name: str) -> str:
    body = re.search(rf"function {name}\([^)]*\) \{{(.*?)\n\}}", js, re.S)
    assert body is not None, name
    return body.group(1)


# ---- #337 : the role follows the seat of the game shown


def test_the_seat_sets_the_role_at_each_game_change() -> None:
    state = _js("_state.js.jinja")
    role = _function_body(state, "seatRole")
    assert "if (colors.length === 1) return 'player_' + colors[0];" in role
    # No seat : a player's role is dropped, arbiter or post_mortem chosen by hand stays.
    assert "return viewerRole().startsWith('player_') ? 'spectator' : viewerRole();" in role
    apply = _function_body(state, "applySeatRole")
    assert "roleSelect.value = role;" in apply
    assert "updateUrl(); applyOrientation(); pollEngine();" in apply


def test_the_seat_is_read_for_the_new_game_token_or_not() -> None:
    """refreshGameEnd runs at each game change (startEventStream) : it decides the role."""
    end = _function_body(_js("_game_end.js.jinja"), "refreshGameEnd")
    assert "if (!playerToken()) { applySeatRole([]); return; }" in end
    # A refused or failed seat read is no seat either, never the previous game's.
    assert "GAME_END.colors = r.ok ? ((await r.json()).colors || []) : [];" in end
    assert end.index("if (gameId() !== game) return;") < end.index(
        "applySeatRole(GAME_END.colors);"
    )
    # A read that throws (network down, server restarting) : no seat, for this game only.
    caught = end[end.index("} catch (_) {") :]
    assert "if (gameId() === game) applySeatRole([]);" in caught
    assert "refreshGameEnd();" in _function_body(_js("_live.js.jinja"), "startEventStream")


def test_the_last_move_is_the_games_own() -> None:
    draw = _function_body(_js("_render.js.jinja"), "draw")
    assert (
        "const last = POS[idx].from && POS[idx].to ? [POS[idx].from, POS[idx].to] : undefined;"
        in draw
    )
    assert "window.__cgSetFen(fen, atLive ? turn : null, atLive, last);" in draw
    board = _js("_board.js.jinja")
    # A new game (resetAndPoll → __cgSetFen(null…)) starts without any highlight.
    assert "cg.set({ fen: 'start', lastMove: undefined, movable:" in board
    assert "lastMove: lastMove || undefined," in board


# ---- #332 point 1 : no silent drop


def test_a_game_over_stays_playable_so_the_server_can_say_so() -> None:
    draw = _function_body(_js("_render.js.jinja"), "draw")
    assert "const atLive = idx === POS.length - 1;" in draw
    assert "lastResult" not in draw


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid, kind in (("stephane", "human"), ("bot-maia-1100", "agent")):
        registry.add_player(games_dir, pid, kind=kind)
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    return issued


def _game(client: TestClient, tokens: dict) -> str:
    r = client.post(
        "/api/games",
        json={"opponent": "bot-maia-1100", "color": "white", "time_control": {"format": "unrated"}},
        headers={"Authorization": f"Bearer {tokens['stephane']}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["game_id"]


def _move(client: TestClient, game_id: str, token: str, **payload: str):
    return client.post(
        f"/api/games/{game_id}/move", json=payload, headers={"Authorization": f"Bearer {token}"}
    )


def test_a_drop_of_the_side_not_to_move_is_said(client: TestClient, tokens: dict) -> None:
    """S12 case 6 : the board blocked it ; now the server answers, in French."""
    game_id = _game(client, tokens)
    r = _move(client, game_id, tokens["stephane"], uci="e7e5")  # Black's pawn, White to move
    assert r.status_code == 422
    assert r.json()["detail"] == refusals.not_possible("e5")


def test_a_drop_on_a_game_over_is_said(client: TestClient, tokens: dict) -> None:
    """S12 case 7 : the pieces were locked without a word."""
    game_id = _game(client, tokens)
    client.post(
        f"/api/games/{game_id}/resign", headers={"Authorization": f"Bearer {tokens['stephane']}"}
    )
    r = _move(client, game_id, tokens["stephane"], uci="e2e4")
    assert (r.status_code, r.json()["detail"]) == (409, refusals.GAME_OVER)


# ---- #340 : the domain's texts, the same on every medium


def test_the_viewer_refusals_are_the_domains() -> None:
    assert viewer_refusals.GAME_OVER is refusals.GAME_OVER
    assert viewer_refusals.not_possible is refusals.not_possible
    assert games_router._MOVE_REFUSALS is refusals.MOVE_REFUSALS


@pytest.mark.asyncio
async def test_a_san_written_wrong_reads_the_same_in_the_viewer_and_the_mcp(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    """A ghost capture : the move Nf3 exists, Nxf3 takes nothing."""
    game_id = _game(client, tokens)
    r = _move(client, game_id, tokens["stephane"], san="Nxf3")
    expected = "Coup refusé : Cxf3 ne prend rien : écrivez Cf3."
    assert (r.status_code, r.json()["detail"]) == (422, expected)
    body = _decode(await call_tool("chess_play_move", {"game_id": game_id, "move_san": "Nxf3"}))
    assert (body["code"], body["reason"]) == ("ILLEGAL_MOVE", expected)
    assert GameState.load(game_id, games_dir=games_dir).san_history == []


def test_an_unreadable_exercise_move_is_said_in_french(tmp_path: Path) -> None:
    """coach/play.py raised « Move 'zz9' rejected: … » : relayed as is by the viewer now."""
    add_exercises(tmp_path, HUMAN, [dict(EXERCISE), dict(ATTACKED)])
    game_id = play.start(tmp_path, HUMAN, "find-1")["game_id"]
    state = GameState.load(game_id, games_dir=tmp_path)
    with pytest.raises(IllegalMoveError) as refused:
        play.play_move(state, HUMAN, "zz9", tmp_path)
    assert str(refused.value) == "Coup refusé : zz9 n'est pas possible dans cette position."
