"""The flag at 0:00, every game (#331, owner 11/11).

Seen in S12 (``smoke-s12-b``, an agents' game) : at 0:00 nothing ended the
game, the next move was written in the PGN, then the game ended « time
forfeit » with ``white_seconds`` at -1125. Now a player's (or the arbiter's)
read ends any game on time, and a move played after the flag is refused,
never written, the time stored at 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard import chessboard_clock, mcp_chessboard_server
from palamede_chessboard.chessboard_clock import init_and_save_clock, load_clock
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.clock_moves import time_out_message
from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.players import registry
from tests.mcp.conftest import _decode

GAME = "agents-331"
LOST = "Temps écoulé : les Blancs ont perdu au temps"


@pytest.fixture
def wall(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1_000_000.0]
    monkeypatch.setattr(chessboard_clock, "_now", lambda: now[0])
    return now


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid, kind in (("stephane", "human"), ("bot-maia-1100", "agent"), ("patrice", "human")):
        registry.add_player(games_dir, pid, kind=kind)
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    return issued


async def _started_game(games_dir: Path, wall: list[float], fen: str | None = None) -> None:
    """An agents' game, not ``tablier.*`` : 1.e4 e5 played, White to move, 1+0."""
    GameState.create(
        GameMetadata(
            game_id=GAME,
            white="stephane",
            black="bot-maia-1100",
            time_control=TimeControl(format="1+0"),
        ),
        games_dir=games_dir,
        starting_fen=fen,
    )
    init_and_save_clock(GAME, TimeControl(format="1+0"), games_dir=games_dir)
    assert _decode(await call_tool("chess_clock_start", {"game_id": GAME}))["status"] == "ok"
    if fen is None:
        for san in ("e4", "e5"):
            wall[0] += 1
            assert (
                _decode(await call_tool("chess_play_move", {"game_id": GAME, "move_san": san}))[
                    "status"
                ]
                == "ok"
            )


def _read(client: TestClient, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(f"/api/clock?game_id={GAME}", headers=headers).json()["clock"]


@pytest.mark.asyncio
async def test_a_players_read_ends_an_agents_game_at_0_00(
    client: TestClient, tokens: dict, games_dir: Path, wall: list[float]
) -> None:
    await _started_game(games_dir, wall)
    wall[0] += 58  # 1.e4 took 1 s : 1 s left
    assert _read(client, tokens["stephane"])["flag_fall"] is None
    wall[0] += 1
    body = _read(client, tokens["bot-maia-1100"])
    assert (body["flag_fall"], body["white_seconds"], body["active"]) == ("white", 0, None)
    state = GameState.load(GAME, games_dir=games_dir)
    assert (state.metadata.result, state.metadata.termination) == ("0-1", "time forfeit")


@pytest.mark.asyncio
@pytest.mark.parametrize("who", [None, "patrice"])
async def test_a_spectators_read_still_changes_nothing(
    client: TestClient, tokens: dict, games_dir: Path, wall: list[float], who: str | None
) -> None:
    await _started_game(games_dir, wall)
    wall[0] += 3600
    assert _read(client, tokens[who] if who else None)["flag_fall"] is None
    assert GameState.load(GAME, games_dir=games_dir).metadata.result == "*"


@pytest.mark.asyncio
async def test_chess_clock_state_with_a_players_token_ends_it_too(
    tokens: dict, games_dir: Path, wall: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _started_game(games_dir, wall)
    wall[0] += 60
    headers = {"authorization": f"Bearer {tokens['stephane']}"}
    monkeypatch.setattr(mcp_chessboard_server, "_request_headers", lambda: headers)
    body = _decode(await call_tool("chess_clock_state", {"game_id": GAME}))
    assert body["clock"]["flag_fall"] == "white"
    assert GameState.load(GAME, games_dir=games_dir).metadata.result == "0-1"


@pytest.mark.asyncio
async def test_a_viewer_move_after_the_flag_is_refused_and_not_written(
    client: TestClient, tokens: dict, games_dir: Path, wall: list[float]
) -> None:
    await _started_game(games_dir, wall)
    wall[0] += 60 + 1125  # the S12 case : 0:00 long passed, nobody read the clock
    r = client.post(
        f"/api/games/{GAME}/move",
        json={"uci": "g1f3"},
        headers={"Authorization": f"Bearer {tokens['stephane']}"},
    )
    assert (r.status_code, r.json()["detail"]) == (409, LOST)
    state = GameState.load(GAME, games_dir=games_dir)
    assert state.san_history == ["e4", "e5"]
    assert (state.metadata.result, state.metadata.termination) == ("0-1", "time forfeit")
    clock = load_clock(GAME, games_dir=games_dir)
    assert (clock.flag_fall, clock.white_seconds, clock.active) == ("white", 0, None)


@pytest.mark.asyncio
async def test_an_mcp_move_after_the_flag_is_refused_and_not_written(
    games_dir: Path, wall: list[float]
) -> None:
    await _started_game(games_dir, wall)
    wall[0] += 60
    body = _decode(await call_tool("chess_play_move", {"game_id": GAME, "move_san": "Nf3"}))
    assert (body["status"], body["code"], body["reason"]) == ("error", "INVALID_STATE", LOST)
    assert GameState.load(GAME, games_dir=games_dir).san_history == ["e4", "e5"]
    assert load_clock(GAME, games_dir=games_dir).white_seconds == 0
    # The game is over : the next attempt is the plain « game over » refusal.
    again = _decode(await call_tool("chess_play_move", {"game_id": GAME, "move_san": "Nf3"}))
    assert again["status"] == "error" and LOST not in again["reason"]


@pytest.mark.asyncio
async def test_a_late_move_against_no_mating_material_says_draw(
    games_dir: Path, wall: list[float]
) -> None:
    """FIDE 6.9 : Black, a lone king and bishop, cannot mate White."""
    await _started_game(games_dir, wall, fen="2b1k3/8/8/8/8/8/8/3QK3 w - - 0 1")
    wall[0] += 60
    body = _decode(await call_tool("chess_play_move", {"game_id": GAME, "move_san": "Qd2"}))
    assert "Temps écoulé : nulle au temps" in body["reason"]
    assert GameState.load(GAME, games_dir=games_dir).metadata.result == "1/2-1/2"


def test_the_message_names_the_side_that_lost() -> None:
    assert time_out_message("1-0", "black") == "Temps écoulé : les Noirs ont perdu au temps"
    assert time_out_message("0-1", "white") == LOST


def _viewer_js(name: str) -> str:
    from palamede_chessboard.viewer.models import TEMPLATES_DIR

    return (TEMPLATES_DIR / "viewer" / name).read_text(encoding="utf-8")


def test_the_viewer_says_on_time_next_to_the_result() -> None:
    poll = _viewer_js("_poll.js.jinja")
    assert "const END_REASONS = { 'time forfeit': 'au temps' };" in poll
    assert "h.Result + endReason(h.Termination) + '</span>'" in poll


def test_the_viewer_redraws_a_game_ended_without_a_move() -> None:
    assert (
        "if (POS.length !== oldLen || ended) { draw(); list(); upd(); pollEngine(); }"
        in _viewer_js("_poll.js.jinja")
    )


def test_a_finished_game_has_no_side_to_move() -> None:
    render = _viewer_js("_render.js.jinja")
    assert "const over = lastResult !== '*' && idx === POS.length - 1;" in render
    assert "textContent = over ? 'Partie terminée' : (next ? 'Trait aux ' + next : '');" in render


@pytest.mark.asyncio
async def test_a_game_already_over_keeps_its_clock(games_dir: Path, wall: list[float]) -> None:
    """Ended before #287 : its clock still names a side to move. A late move
    there is the plain « game over » refusal, no flag written."""
    await _started_game(games_dir, wall)
    state = GameState.load(GAME, games_dir=games_dir)
    state.metadata.result, state.metadata.termination = "1-0", "normal"
    state.save()
    wall[0] += 3600
    body = _decode(await call_tool("chess_play_move", {"game_id": GAME, "move_san": "Nf3"}))
    assert body["status"] == "error" and LOST not in body["reason"]
    clock = load_clock(GAME, games_dir=games_dir)
    assert (clock.flag_fall, clock.active) == (None, "white")


@pytest.mark.asyncio
async def test_the_pgn_ends_before_the_clock_is_written(
    games_dir: Path, wall: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Admin #338 : a stop between the two writes leaves a game over
    with a running clock (shown stopped, #287), never a flag on a live game."""
    from palamede_chessboard import clock_moves

    await _started_game(games_dir, wall)
    wall[0] += 60

    def stop(*_args, **_kwargs) -> None:
        raise RuntimeError("stopped between the two writes")

    monkeypatch.setattr(clock_moves, "save_clock", stop)
    await call_tool("chess_play_move", {"game_id": GAME, "move_san": "Nf3"})
    assert GameState.load(GAME, games_dir=games_dir).metadata.result == "0-1"
    assert load_clock(GAME, games_dir=games_dir).flag_fall is None
