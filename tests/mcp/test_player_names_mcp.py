"""``chess_create_game`` refuses a player name no account can carry (#62).

The viewer authorizes a move by the token's ``player_id`` against the
normalised ``White`` / ``Black`` ; a name the registry refuses would make
a game whose players are answered 403 forever.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode


def _args(game_id: str, white: object, black: object) -> dict:
    return {"game_id": game_id, "white": white, "black": black, "rated": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    ["Stéphane", "Joueur Un", "", "   ", "-leader", "a" * 33, 42, None],
)
async def test_a_name_no_account_can_carry_creates_no_game(games_dir: Path, name: object) -> None:
    body = _decode(await call_tool("chess_create_game", _args("nameless", name, "leader")))
    assert body["status"] == "error"
    assert body["code"] == "CHESSBOARD_ERROR"
    assert body["reason"].startswith("white ")
    assert not (games_dir / "nameless.pgn").exists()


@pytest.mark.asyncio
async def test_black_is_checked_too(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_create_game", _args("nameless", "junior", "Joueur Un")))
    assert body["reason"].startswith("black 'Joueur Un'")
    assert not (games_dir / "nameless.pgn").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("white", "black"),
    [("Leader", "Junior"), ("devleader", "claude-desktop"), ("a" * 32, "bot-maia-1900")],
)
async def test_a_name_that_normalises_to_an_id_is_kept_as_given(
    games_dir: Path, white: str, black: str
) -> None:
    body = _decode(await call_tool("chess_create_game", _args("named", white, black)))
    assert body["status"] == "ok"
    assert body["state"]["metadata"]["white"] == white
    assert body["state"]["metadata"]["black"] == black
