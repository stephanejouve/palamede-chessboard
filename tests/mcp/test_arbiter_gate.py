"""Tests for `palamede_chessboard.mcp_chessboard_server` — chess_create_game arbiter contract (IA id / daemon pidfile).

Carved out of the pre-split 1471l test_mcp_chessboard_server.py.
Shared `games_dir` fixture lives in tests/mcp/conftest.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode

# --- arbiter contract at create time --------------------------------------


@pytest.mark.asyncio
async def test_create_with_ia_arbiter_ok(games_dir: Path) -> None:
    """An IA agent id in ``arbiter`` satisfies the contract even without
    a running daemon."""
    (games_dir / ".arbiter-daemon.pid").unlink()  # simulate no daemon
    body = _decode(
        await call_tool(
            "chess_create_game",
            {
                "game_id": "arb-ia",
                "white": "w",
                "black": "b",
                "arbiter": "leader-bot",
            },
        )
    )
    assert body["status"] == "ok"
    assert body["state"]["metadata"]["arbiter"] == "leader-bot"


@pytest.mark.asyncio
async def test_create_without_arbiter_or_daemon_rejected(games_dir: Path) -> None:
    """Contract : reject when both routes fail (no IA arbiter, no daemon)."""
    (games_dir / ".arbiter-daemon.pid").unlink()
    body = _decode(
        await call_tool(
            "chess_create_game",
            {"game_id": "arb-none", "white": "w", "black": "b"},
        )
    )
    assert body["status"] == "error"
    assert body["code"] == "CHESSBOARD_ERROR"
    assert "Arbiter required" in body["reason"]


@pytest.mark.asyncio
async def test_create_without_arbiter_but_daemon_ok(games_dir: Path) -> None:
    """Contract : daemon pidfile alone is enough (fixture already writes it)."""
    body = _decode(
        await call_tool(
            "chess_create_game",
            {"game_id": "arb-daemon", "white": "w", "black": "b"},
        )
    )
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_create_with_stale_daemon_pidfile_rejected(games_dir: Path) -> None:
    """Contract : a pidfile whose PID is dead does NOT satisfy the daemon
    branch — otherwise a crashed daemon would silently keep letting new
    games in."""
    # Overwrite the fixture's live pidfile with a definitely-dead PID.
    # PID 999999999 is well beyond typical pid_max on macOS/Linux.
    (games_dir / ".arbiter-daemon.pid").write_text("999999999", encoding="utf-8")
    body = _decode(
        await call_tool(
            "chess_create_game",
            {"game_id": "arb-stale", "white": "w", "black": "b"},
        )
    )
    assert body["status"] == "error"
    assert body["code"] == "CHESSBOARD_ERROR"


@pytest.mark.asyncio
async def test_create_with_malformed_daemon_pidfile_rejected(games_dir: Path) -> None:
    """Garbage in the pidfile is treated as no daemon."""
    (games_dir / ".arbiter-daemon.pid").write_text("not-an-int\n", encoding="utf-8")
    body = _decode(
        await call_tool(
            "chess_create_game",
            {"game_id": "arb-malformed", "white": "w", "black": "b"},
        )
    )
    assert body["status"] == "error"
    assert body["code"] == "CHESSBOARD_ERROR"


@pytest.mark.asyncio
async def test_daemon_alive_but_owned_by_other_user_accepted(
    games_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-user contract (CD bug report 2026-09-30) : the arbiter daemon
    typically runs as ``magma`` while MCP servers spawn under other users
    (``stephanejouve`` for Leader / Claude Desktop, ``admin``). ``os.kill(pid, 0)``
    on a PID owned by another user raises ``PermissionError`` — meaning
    the process EXISTS but we can't signal it. This is a "daemon alive"
    signal, not "daemon dead".
    """
    from palamede_chessboard import mcp_chessboard_server

    # Arbitrary PID that the fixture wrote (fixture uses os.getpid() = alive).
    # Force os.kill to raise PermissionError to simulate cross-user ownership.
    def _fake_kill(_pid: int, _sig: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(mcp_chessboard_server.os, "kill", _fake_kill)

    body = _decode(
        await call_tool(
            "chess_create_game",
            {"game_id": "arb-xuser", "white": "w", "black": "b"},
        )
    )
    # Must accept — cross-user daemon is a valid signal of availability.
    assert body["status"] == "ok"
    assert body["state"]["metadata"]["arbiter"] is None


@pytest.mark.asyncio
async def test_create_with_empty_arbiter_falls_back_to_daemon(games_dir: Path) -> None:
    """Empty-string arbiter is treated as absent; the daemon path takes over."""
    body = _decode(
        await call_tool(
            "chess_create_game",
            {"game_id": "arb-empty", "white": "w", "black": "b", "arbiter": "   "},
        )
    )
    # Daemon pidfile from fixture is still there → OK.
    assert body["status"] == "ok"
