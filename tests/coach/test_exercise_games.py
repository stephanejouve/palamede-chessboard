"""Exercise games outside the viewer (#95) : reserved, refused by the MCP.

The ``exo.`` prefix is the server's : no creation whose id comes from
outside may take it, and the MCP, which takes ``by`` as given, changes
no such game. The older ``exo-…`` games (a dash) stay ordinary games.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from palamede_chessboard import cli_matchmaking
from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameMetadata,
    GameState,
    TimeControl,
)
from palamede_chessboard.coach import play
from palamede_chessboard.coach.diagnosis import run_once
from palamede_chessboard.coach.store import add_exercises, read_exercises, update_exercise
from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.paths import RESERVED_GAME_ID_PREFIXES, refuse_reserved_game_id
from palamede_chessboard.players import registry
from tests.mcp.conftest import _decode


@pytest.fixture
def mcp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    return tmp_path


def _game(gdir: Path, game_id: str, white: str = "w", black: str = "b") -> GameState:
    meta = GameMetadata(
        game_id=game_id, white=white, black=black, time_control=TimeControl(format="unrated")
    )
    return GameState.create(meta, games_dir=gdir)


@pytest.mark.parametrize("game_id", ["exo.human-player.20261104-200000-abcd", "EXO.x", "Exo.y"])
def test_the_prefix_is_reserved(game_id: str) -> None:
    with pytest.raises(ChessboardError, match="exercises of the coach"):
        refuse_reserved_game_id(game_id)


@pytest.mark.parametrize("game_id", ["exo-20261020-mat", "exo", "exodus.1", "tablier-like"])
def test_older_and_other_ids_are_free(game_id: str) -> None:
    refuse_reserved_game_id(game_id)
    assert play.is_exercise_game(game_id) is False


def test_tablier_stays_reserved() -> None:
    assert set(RESERVED_GAME_ID_PREFIXES) == {"tablier.", "exo."}


@pytest.mark.asyncio
async def test_the_mcp_never_creates_one(mcp_dir: Path) -> None:
    body = _decode(
        await call_tool(
            "chess_create_game",
            {
                "game_id": "exo.human-player.x",
                "white": "a",
                "black": "b",
                "rated": False,
                "arbiter": "agent-player",
            },
        )
    )
    assert body["status"] == "error" and "reserved" in body["reason"]
    assert not list(mcp_dir.glob("*.pgn"))


@pytest.mark.asyncio
async def test_an_older_exo_game_is_still_created_and_played(mcp_dir: Path) -> None:
    created = await call_tool(
        "chess_create_game",
        {
            "game_id": "exo-20261020",
            "white": "a",
            "black": "b",
            "rated": False,
            "arbiter": "agent-player",
        },
    )
    assert _decode(created)["status"] == "ok"
    played = await call_tool("chess_play_move", {"game_id": "exo-20261020", "move_san": "e4"})
    assert _decode(played)["status"] == "ok"
    resigned = await call_tool("chess_resign", {"game_id": "exo-20261020", "by": "black"})
    assert _decode(resigned)["status"] == "ok"


#: Every MCP tool that changes a game, written out : not read from the set tested.
CHANGING_TOOLS = [
    "chess_play_move",
    "chess_resign",
    "chess_offer_draw",
    "chess_agree_draw",
    "chess_decline_draw",
]


@pytest.mark.parametrize("tool", CHANGING_TOOLS)
@pytest.mark.asyncio
async def test_the_mcp_changes_no_exercise_game(mcp_dir: Path, tool: str) -> None:
    game_id = "exo.human-player.20261104-200000-abcd"
    _game(mcp_dir, game_id, white=play.OPPONENT, black="human-player")
    arguments = {"game_id": game_id, "move_san": "e4", "by": "human-player"}
    body = _decode(await call_tool(tool, arguments))
    assert body == {
        "status": "error",
        "code": "EXERCISE_VIEWER_ONLY",
        "reason": "an exercise game is played from the viewer, under the player's token",
    }
    state = GameState.load(game_id, games_dir=mcp_dir)
    assert state.san_history == [] and state.metadata.result == "*"
    assert state.metadata.draw_offer is None


@pytest.mark.asyncio
async def test_the_mcp_still_reads_an_exercise_game(mcp_dir: Path) -> None:
    game_id = "Exo.human-player.x"
    _game(mcp_dir, game_id)
    body = _decode(await call_tool("chess_view_state", {"game_id": game_id}))
    assert body["status"] == "ok"
    body = _decode(await call_tool("chess_play_move", {"game_id": game_id, "move_san": "e4"}))
    assert body["code"] == "EXERCISE_VIEWER_ONLY"  # whatever its case


def test_matchmaking_never_creates_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match = SimpleNamespace(white="a", black="b")
    monkeypatch.setattr(
        cli_matchmaking, "schedule_round_from_disk", lambda **_: SimpleNamespace(matches=[match])
    )
    monkeypatch.setattr(cli_matchmaking, "_default_game_id", lambda *_: "exo.a.1")
    args = argparse.Namespace(
        games_dir=tmp_path, round=1, time_control="unrated", event="", site="", json=True
    )
    with pytest.raises(ChessboardError, match="reserved"):
        cli_matchmaking._cmd_create(args)
    assert not list(tmp_path.glob("*.pgn"))


def test_the_coach_does_not_diagnose_its_exercises(tmp_path: Path) -> None:
    registry.add_player(tmp_path, "human-player", kind="human")
    state = _game(tmp_path, "exo.human-player.x", white=play.OPPONENT, black="human-player")
    state.resign(by="black")
    # A finished game of a human : without the skip, the missing engine
    # would put it in ``failed``.
    assert run_once(tmp_path, engine_path="/no-engine") == {
        "diagnosed": [],
        "waiting": [],
        "failed": [],
        "exercises": 0,
    }


def test_updating_an_unknown_exercise(tmp_path: Path) -> None:
    add_exercises(tmp_path, "human-player", [{"id": "e1"}])
    with pytest.raises(KeyError):
        update_exercise(tmp_path, "human-player", "e2", lambda e: e.update(state="solved"))
    assert read_exercises(tmp_path, "human-player") == [{"id": "e1"}]


def test_public_view_hides_the_solution_until_solved() -> None:
    exercise = {
        "id": "e",
        "state": "failed",
        "solution_uci": "a2a3",
        "solution_san": "a3",
        "solution_squares": ["f6"],
        "slower_mates": ["b2b3"],
        "gap_cp": 300,
    }
    assert play.public_view(exercise) == {"id": "e", "state": "failed", "gap_cp": 300}
    exercise["state"] = "solved"
    assert play.public_view(exercise) == exercise
