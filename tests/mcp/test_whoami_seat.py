"""chess_whoami and chess_seat : the MCP twins of /api/me and /seat (#266)."""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard import mcp_chessboard_server
from palamede_chessboard._mcp_tool_handlers import TOOL_HANDLERS
from palamede_chessboard._mcp_tool_schemas import TOOL_SCHEMAS
from palamede_chessboard.mcp_chessboard_server import call_tool
from palamede_chessboard.players import registry
from palamede_chessboard.players import tokens as token_format
from tests.mcp.conftest import _decode


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid, kind in (("stephane", "human"), ("leader", "agent"), ("junior", "agent")):
        registry.add_player(games_dir, pid, kind=kind)
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    return issued


@pytest.fixture
def as_token(monkeypatch: pytest.MonkeyPatch):
    """Make the next MCP calls carry ``Authorization`` like the HTTP transport."""

    def use(value: str | None, scheme: str = "Bearer") -> None:
        headers = {"authorization": f"{scheme} {value}"} if value else {}
        monkeypatch.setattr(mcp_chessboard_server, "_request_headers", lambda: headers)

    return use


async def _call(name: str, arguments: dict | None = None) -> dict:
    return _decode(await call_tool(name, arguments or {}))


async def _game(white: str = "stephane", black: str = "leader") -> str:
    created = await _call(
        "chess_create_game", {"game_id": "seat-test", "white": white, "black": black}
    )
    assert created["status"] == "ok", created
    return "seat-test"


# ---- chess_whoami ------------------------------------------------------------


@pytest.mark.parametrize(("pid", "kind"), [("stephane", "human"), ("leader", "agent")])
async def test_whoami_names_the_token_s_player(tokens, as_token, pid, kind) -> None:
    as_token(tokens[pid])
    assert await _call("chess_whoami") == {
        "status": "ok",
        "authenticated": True,
        "player": pid,
        "kind": kind,
    }


@pytest.mark.parametrize("token", [None, "pas-un-jeton"], ids=["absent", "malformé"])
async def test_whoami_without_a_valid_token_is_anonymous_not_an_error(
    tokens, as_token, token
) -> None:
    as_token(token)
    answer = await _call("chess_whoami")
    assert answer == {"status": "ok", "authenticated": False, "player": None, "kind": None}


async def test_whoami_unknown_revoked_or_other_scheme_is_anonymous(
    tokens, as_token, games_dir: Path
) -> None:
    _id, secret = token_format.parse_token(tokens["stephane"])
    as_token(token_format.format_token("00000000", secret))
    assert (await _call("chess_whoami"))["authenticated"] is False
    as_token(tokens["stephane"], scheme="Basic")
    assert (await _call("chess_whoami"))["authenticated"] is False
    token_id, _secret = token_format.parse_token(tokens["leader"])
    registry.revoke_token(games_dir, "leader", token_id)
    as_token(tokens["leader"])
    assert (await _call("chess_whoami"))["authenticated"] is False


async def test_whoami_of_a_token_whose_player_left_the_registry(
    tokens, as_token, monkeypatch
) -> None:
    """A token that still verifies but names no account is anonymous, not a player."""
    monkeypatch.setattr(registry, "verify_token", lambda _gdir, _token: "fantome")
    as_token(tokens["stephane"])
    assert (await _call("chess_whoami"))["authenticated"] is False


async def test_whoami_over_stdio_is_anonymous(tokens, monkeypatch) -> None:
    monkeypatch.setattr(mcp_chessboard_server, "_request_headers", lambda: None)
    assert (await _call("chess_whoami"))["authenticated"] is False


async def test_whoami_with_an_unreadable_registry_is_an_error(
    tokens, as_token, monkeypatch
) -> None:
    def broken(*_args, **_kwargs):
        raise registry.RegistryError("illisible")

    monkeypatch.setattr(registry, "verify_token", broken)
    as_token(tokens["stephane"])
    assert await _call("chess_whoami") == {"status": "error", "code": "REGISTRY_UNREADABLE"}


# ---- chess_seat --------------------------------------------------------------


async def test_seat_gives_the_token_s_side(tokens, as_token) -> None:
    game_id = await _game()
    as_token(tokens["leader"])
    assert await _call("chess_seat", {"game_id": game_id}) == {
        "status": "ok",
        "game_id": game_id,
        "player": "leader",
        "colors": ["black"],
    }


async def test_seat_of_a_player_holding_both_sides(tokens, as_token) -> None:
    game_id = await _game(white="stephane", black="stephane")
    as_token(tokens["stephane"])
    assert (await _call("chess_seat", {"game_id": game_id}))["colors"] == ["white", "black"]


async def test_seat_refuses_a_token_outside_the_game(tokens, as_token) -> None:
    game_id = await _game()
    as_token(tokens["junior"])
    answer = await _call("chess_seat", {"game_id": game_id})
    assert answer["status"] == "error" and answer["code"] == "NOT_A_PLAYER"


async def test_seat_without_a_valid_token_is_unauthenticated(tokens, as_token) -> None:
    game_id = await _game()
    as_token(None)
    answer = await _call("chess_seat", {"game_id": game_id})
    assert answer["status"] == "error" and answer["code"] == "UNAUTHENTICATED"


async def test_seat_of_an_unknown_game(tokens, as_token) -> None:
    as_token(tokens["stephane"])
    answer = await _call("chess_seat", {"game_id": "absente"})
    assert answer["status"] == "error" and answer["code"] == "GAME_NOT_FOUND"


# ---- registry of the tools -----------------------------------------------------


def test_every_tool_routes_to_its_own_handler() -> None:
    """A tool routed to another's handler would pass unseen (review Junior #270)."""
    for name, handler in TOOL_HANDLERS.items():
        assert handler.__name__ == f"_handle_{name}", name


def test_every_schema_has_a_handler_and_back() -> None:
    assert {tool.name for tool in TOOL_SCHEMAS} == set(TOOL_HANDLERS)
