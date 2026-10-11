"""Tests for ``palamede_chessboard.mcp_chessboard_server`` tool handlers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool


def _decode(result) -> dict:
    """Strip the wrapping ``[TextContent]`` and JSON-parse the payload."""
    assert len(result) == 1
    payload = json.loads(result[0].text)
    payload.pop("_metadata", None)
    return payload


@pytest.fixture
def games_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    # Redirect the per-user board-facts journal into the test's tmp_path
    # so tests don't touch the real ``~/.local/share/outillages/`` and
    # can assert on the file's presence/content cleanly.
    monkeypatch.setenv("OUTILLAGES_BOARD_FACTS_LOG", str(tmp_path / "board_facts_log.jsonl"))
    # Isolate the engine-call audit log too — the guard writes one line
    # per guarded tool call and we don't want to pollute the real
    # ``~/.local/share/outillages/engine_calls_log.jsonl``.
    monkeypatch.setenv("OUTILLAGES_ENGINE_CALLS_LOG", str(tmp_path / "engine_calls_log.jsonl"))
    # Publish a fake arbiter-daemon pidfile pointing at the current test
    # process so ``chess_create_game`` accepts calls that omit the
    # ``arbiter`` field. Individual tests that want to verify the
    # arbiter-required contract can delete the file explicitly.
    import os as _os

    (tmp_path / ".arbiter-daemon.pid").write_text(str(_os.getpid()), encoding="utf-8")
    return tmp_path


@pytest.mark.asyncio
async def test_create_view_play_flow(games_dir: Path) -> None:
    created = await call_tool(
        "chess_create_game",
        {
            "game_id": "smoke",
            "white": "Junior",
            "black": "Tephra",
            "arbiter": "Admin",
            "event": "Smoke test",
            "time_control": {"format": "5+3", "start_seconds": 300, "increment_seconds": 3},
        },
    )
    body = _decode(created)
    assert body["status"] == "ok"
    assert body["state"]["metadata"]["arbiter"] == "Admin"
    assert body["state"]["ply"] == 0
    assert body["state"]["turn"] == "white"

    viewed = _decode(await call_tool("chess_view_state", {"game_id": "smoke"}))
    assert viewed["status"] == "ok"
    assert viewed["state"]["ply"] == 0

    played = _decode(
        await call_tool(
            "chess_play_move",
            {"game_id": "smoke", "move_san": "e4", "by": "Junior"},
        )
    )
    assert played["status"] == "ok"
    assert played["move"]["san"] == "e4"
    assert played["state"]["ply"] == 1
    assert played["state"]["turn"] == "black"


@pytest.mark.asyncio
async def test_illegal_move_returns_error(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "illegal", "white": "w", "black": "b"},
    )
    body = _decode(await call_tool("chess_play_move", {"game_id": "illegal", "move_san": "Ke5"}))
    assert body["status"] == "error"
    assert body["code"] == "ILLEGAL_MOVE"


@pytest.mark.asyncio
async def test_play_move_expected_ply_match_succeeds(games_dir: Path) -> None:
    """``expected_ply`` matching current ply = normal path (chess-autoplay #270)."""
    await call_tool("chess_create_game", {"game_id": "exp-ok", "white": "w", "black": "b"})
    body = _decode(
        await call_tool(
            "chess_play_move",
            {"game_id": "exp-ok", "move_san": "e4", "expected_ply": 0},
        )
    )
    assert body["status"] == "ok"
    assert body["state"]["ply"] == 1


@pytest.mark.asyncio
async def test_play_move_stale_ply_returns_error(games_dir: Path) -> None:
    """Concurrent writer advanced the state ; stale caller gets STALE_PLY
    with both ply values exposed, and the second move is NOT applied."""
    await call_tool("chess_create_game", {"game_id": "exp-stale", "white": "w", "black": "b"})
    await call_tool("chess_play_move", {"game_id": "exp-stale", "move_san": "e4"})
    body = _decode(
        await call_tool(
            "chess_play_move",
            {"game_id": "exp-stale", "move_san": "e5", "expected_ply": 0},
        )
    )
    assert body["status"] == "error"
    assert body["code"] == "STALE_PLY"
    assert body["current_ply"] == 1
    assert body["expected_ply"] == 0

    # Board didn't advance : still only e4 played.
    viewed = _decode(await call_tool("chess_view_state", {"game_id": "exp-stale"}))
    assert viewed["state"]["ply"] == 1


@pytest.mark.asyncio
async def test_play_move_expected_ply_omitted_backward_compat(games_dir: Path) -> None:
    """Callers that never set ``expected_ply`` keep the pre-#270 behaviour."""
    await call_tool("chess_create_game", {"game_id": "exp-compat", "white": "w", "black": "b"})
    first = _decode(await call_tool("chess_play_move", {"game_id": "exp-compat", "move_san": "e4"}))
    second = _decode(
        await call_tool("chess_play_move", {"game_id": "exp-compat", "move_san": "e5"})
    )
    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert second["state"]["ply"] == 2


@pytest.mark.asyncio
async def test_create_game_duplicate(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "dup", "white": "w", "black": "b"})
    body = _decode(
        await call_tool("chess_create_game", {"game_id": "dup", "white": "w", "black": "b"})
    )
    assert body["status"] == "error"
    assert body["code"] == "GAME_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_view_state_missing_game(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_view_state", {"game_id": "ghost"}))
    assert body["status"] == "error"
    assert body["code"] == "GAME_NOT_FOUND"


@pytest.mark.asyncio
async def test_commentary_round_trip(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "com", "white": "w", "black": "b"})
    post = _decode(
        await call_tool(
            "chess_post_commentary",
            {"game_id": "com", "ply": 1, "source": "arbiter", "text": "neat"},
        )
    )
    assert post["status"] == "ok"
    listed = _decode(await call_tool("chess_get_commentary", {"game_id": "com"}))
    assert listed["status"] == "ok"
    assert listed["count"] == 1
    assert listed["entries"][0]["text"] == "neat"


@pytest.mark.asyncio
async def test_get_raw_pgn_includes_headers(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {
            "game_id": "pgn",
            "white": "w",
            "black": "b",
            "arbiter": "a",
            "spectators": ["x", "y"],
            "event": "Test",
        },
    )
    body = _decode(await call_tool("chess_get_raw_pgn", {"game_id": "pgn"}))
    assert body["status"] == "ok"
    assert '[White "w"]' in body["pgn"]
    assert '[Arbiter "a"]' in body["pgn"]
    assert '[Spectators "x,y"]' in body["pgn"]


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_nope", {}))
    assert body["status"] == "error"
    assert body["code"] == "UNKNOWN_TOOL"


# ---- #244 : resign + draw offer/agree/decline ----------------------------


@pytest.mark.asyncio
async def test_resign_ends_game(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "res1", "white": "admin", "black": "Leader"},
    )
    await call_tool("chess_play_move", {"game_id": "res1", "move_san": "e4"})
    body = _decode(await call_tool("chess_resign", {"game_id": "res1", "by": "black"}))
    assert body["status"] == "ok"
    assert body["resign"]["result"] == "1-0"
    assert body["state"]["result"] == "1-0"
    assert body["state"]["is_game_over"] is True
    assert body["state"]["termination_note"] == "Leader resigns"


@pytest.mark.asyncio
async def test_resign_by_player_name(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "resbn", "white": "admin", "black": "Leader"},
    )
    body = _decode(await call_tool("chess_resign", {"game_id": "resbn", "by": "Leader"}))
    assert body["status"] == "ok"
    assert body["resign"]["resigned_by"] == "black"


@pytest.mark.asyncio
async def test_resign_twice_returns_invalid_state(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "res2x", "white": "w", "black": "b"},
    )
    await call_tool("chess_resign", {"game_id": "res2x", "by": "white"})
    body = _decode(await call_tool("chess_resign", {"game_id": "res2x", "by": "black"}))
    assert body["status"] == "error"
    assert body["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_resign_unresolvable_by(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "resbad", "white": "w", "black": "b"},
    )
    body = _decode(await call_tool("chess_resign", {"game_id": "resbad", "by": "phantom"}))
    assert body["status"] == "error"
    assert body["code"] == "CHESSBOARD_ERROR"


@pytest.mark.asyncio
async def test_draw_offer_and_agree(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {"game_id": "dr1", "white": "admin", "black": "Leader"},
    )
    await call_tool("chess_play_move", {"game_id": "dr1", "move_san": "e4"})
    offer = _decode(await call_tool("chess_offer_draw", {"game_id": "dr1", "by": "black"}))
    assert offer["status"] == "ok"
    assert offer["offer"]["pending"] is True
    assert offer["state"]["draw_offer"] == {"by": "black", "ply": 1}

    agree = _decode(await call_tool("chess_agree_draw", {"game_id": "dr1", "by": "white"}))
    assert agree["status"] == "ok"
    assert agree["agree"]["result"] == "1/2-1/2"
    assert agree["state"]["result"] == "1/2-1/2"
    assert agree["state"]["draw_offer"] is None
    assert agree["state"]["is_game_over"] is True


@pytest.mark.asyncio
async def test_draw_offer_idempotent(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "drid", "white": "w", "black": "b"})
    _decode(await call_tool("chess_offer_draw", {"game_id": "drid", "by": "white"}))
    second = _decode(await call_tool("chess_offer_draw", {"game_id": "drid", "by": "white"}))
    assert second["status"] == "ok"
    assert second["offer"]["idempotent"] is True


@pytest.mark.asyncio
async def test_agree_without_offer_invalid(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "drno", "white": "w", "black": "b"})
    body = _decode(await call_tool("chess_agree_draw", {"game_id": "drno", "by": "white"}))
    assert body["status"] == "error"
    assert body["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_agree_own_offer_invalid(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "drself", "white": "w", "black": "b"})
    _decode(await call_tool("chess_offer_draw", {"game_id": "drself", "by": "white"}))
    body = _decode(await call_tool("chess_agree_draw", {"game_id": "drself", "by": "white"}))
    assert body["status"] == "error"
    assert body["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_decline_draw_clears_offer(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "drdec", "white": "w", "black": "b"})
    _decode(await call_tool("chess_offer_draw", {"game_id": "drdec", "by": "white"}))
    body = _decode(await call_tool("chess_decline_draw", {"game_id": "drdec", "by": "black"}))
    assert body["status"] == "ok"
    assert body["decline"]["declined_by"] == "black"
    assert body["state"]["draw_offer"] is None
    assert body["state"]["result"] == "*"


@pytest.mark.asyncio
async def test_resign_rewrites_pgn_with_termination(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "pgnres", "white": "admin", "black": "L"})
    await call_tool("chess_resign", {"game_id": "pgnres", "by": "white"})
    body = _decode(await call_tool("chess_get_raw_pgn", {"game_id": "pgnres"}))
    assert body["status"] == "ok"
    assert '[Result "0-1"]' in body["pgn"]
    assert '[Termination "admin resigns"]' in body["pgn"]
    assert "{admin resigns} 0-1" in body["pgn"]


# ---- Phase A : clock wiring ----------------------------------------------


@pytest.mark.asyncio
async def test_clock_created_with_5_plus_3(games_dir: Path) -> None:
    body = _decode(
        await call_tool(
            "chess_create_game",
            {
                "game_id": "clk",
                "white": "w",
                "black": "b",
                "time_control": {"format": "5+3"},
            },
        )
    )
    assert body["status"] == "ok"
    assert body["clock"] is not None
    assert body["clock"]["white_seconds"] == 300
    assert body["clock"]["increment_seconds"] == 3
    assert body["clock"]["started"] is False


@pytest.mark.asyncio
async def test_clock_absent_when_unrated(games_dir: Path) -> None:
    body = _decode(
        await call_tool("chess_create_game", {"game_id": "noclk", "white": "w", "black": "b"})
    )
    assert body["status"] == "ok"
    assert body["clock"] is None


@pytest.mark.asyncio
async def test_clock_start_then_state(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {
            "game_id": "started",
            "white": "w",
            "black": "b",
            "time_control": {"format": "5+3"},
        },
    )
    started = _decode(await call_tool("chess_clock_start", {"game_id": "started"}))
    assert started["status"] == "ok"
    assert started["clock"]["active"] == "white"
    assert started["clock"]["started"] is True

    seen = _decode(await call_tool("chess_clock_state", {"game_id": "started"}))
    assert seen["status"] == "ok"
    assert seen["clock"]["active"] == "white"


@pytest.mark.asyncio
async def test_clock_start_no_op_unrated_returns_error(games_dir: Path) -> None:
    await call_tool("chess_create_game", {"game_id": "unr", "white": "w", "black": "b"})
    body = _decode(await call_tool("chess_clock_start", {"game_id": "unr"}))
    assert body["status"] == "error"
    assert body["code"] == "NO_CLOCK"


@pytest.mark.asyncio
async def test_play_move_advances_clock(games_dir: Path) -> None:
    await call_tool(
        "chess_create_game",
        {
            "game_id": "advance",
            "white": "w",
            "black": "b",
            "time_control": {"format": "5+3"},
        },
    )
    await call_tool("chess_clock_start", {"game_id": "advance"})
    body = _decode(await call_tool("chess_play_move", {"game_id": "advance", "move_san": "e4"}))
    assert body["status"] == "ok"
    # Real wall-clock — assert the clock changed *and* turn flipped.
    assert body["clock"]["active"] == "black"
    assert body["clock"]["white_seconds"] != 300


@pytest.mark.asyncio
async def test_clock_state_missing_game_returns_null(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_clock_state", {"game_id": "ghost"}))
    assert body["status"] == "ok"
    assert body["clock"] is None


# ---- Phase B : engine tools ----------------------------------------------


@pytest.mark.asyncio
async def test_analyze_position_via_fen(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock, patch

    class _Score:
        def is_mate(self):
            return False

        def score(self):
            return 33

    class _Pov:
        def white(self):
            return _Score()

    import chess as _chess

    engine = MagicMock()
    engine.analyse.return_value = {
        "score": _Pov(),
        "pv": [_chess.Move.from_uci("e2e4")],
        "depth": 5,
    }
    cm = MagicMock()
    cm.__enter__.return_value = engine
    cm.__exit__.return_value = False

    # #311 : patcher get_engine_path pour tourner en CI sans stockfish.
    with (
        patch(
            "palamede_chessboard.chessboard_engine.get_engine_path",
            return_value="/bin/true",
        ),
        patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=cm,
        ),
    ):
        body = _decode(
            await call_tool(
                "chess_analyze_position",
                {
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "depth": 5,
                },
            )
        )
    assert body["status"] == "ok"
    assert body["analysis"]["score_cp"] == 33
    assert body["analysis"]["best_move_uci"] == "e2e4"


@pytest.mark.asyncio
async def test_analyze_position_missing_inputs(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_analyze_position", {}))
    assert body["status"] == "error"
    assert body["code"] == "MISSING_INPUT"


@pytest.mark.asyncio
async def test_analyze_game_returns_walk(games_dir: Path) -> None:
    from unittest.mock import MagicMock, patch

    class _Score:
        def __init__(self, cp):
            self._cp = cp

        def is_mate(self):
            return False

        def score(self):
            return self._cp

    class _Pov:
        def __init__(self, cp):
            self._cp = cp

        def white(self):
            return _Score(self._cp)

    import chess as _chess

    # create game with 1 move, then resign — chess_analyze_game is now
    # gated behind the engine guard and refuses in-progress games.
    await call_tool("chess_create_game", {"game_id": "ana-game", "white": "w", "black": "b"})
    await call_tool("chess_play_move", {"game_id": "ana-game", "move_san": "e4"})
    await call_tool("chess_resign", {"game_id": "ana-game", "by": "white"})

    engine = MagicMock()
    engine.analyse.side_effect = [
        {
            "score": _Pov(20),
            "pv": [_chess.Move.from_uci("e2e4")],
            "depth": 5,
        },
        {
            "score": _Pov(25),
            "pv": [_chess.Move.from_uci("e7e5")],
            "depth": 5,
        },
    ]
    cm = MagicMock()
    cm.__enter__.return_value = engine
    cm.__exit__.return_value = False

    # #311 : patcher get_engine_path pour tourner en CI sans stockfish.
    with (
        patch(
            "palamede_chessboard.chessboard_engine.get_engine_path",
            return_value="/bin/true",
        ),
        patch(
            "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
            return_value=cm,
        ),
    ):
        body = _decode(await call_tool("chess_analyze_game", {"game_id": "ana-game", "depth": 5}))
    assert body["status"] == "ok"
    assert body["count"] == 1
    assert body["walk"][0]["mover"] == "white"
    assert body["walk"][0]["san"] == "e4"
    assert body["walk"][0]["classification"] in {
        "good",
        "inaccuracy",
        "mistake",
        "blunder",
    }


@pytest.mark.asyncio
async def test_engine_unavailable_surfaces_error(
    games_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_CHESSBOARD_STOCKFISH", "/definitely/not/here")
    body = _decode(
        await call_tool(
            "chess_analyze_position",
            {
                "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            },
        )
    )
    assert body["status"] == "error"
    assert body["code"] == "ENGINE_UNAVAILABLE"


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


# --- chess_board_facts (CD spec 2026-09-30) ------------------------------


@pytest.mark.asyncio
async def test_board_facts_qb4_hangs_to_bishop_junior_leader_002_ply31(
    games_dir: Path,
) -> None:
    """Regression : la dame b4 doit être flaggée en prise sur cette
    position (junior-vs-leader-002 après 16.Qxb4). Attaquée par Bf8,
    non défendue, attackers de moindre valeur → double critère hanging.
    """
    fen = "r2qkb1r/3n1ppp/p7/3b4/1Q1B2P1/5P2/PPP4P/2KR1B1R b kq - 0 16"
    body = _decode(await call_tool("chess_board_facts", {"fen": fen}))
    assert body["status"] == "ok"
    hanging = body["facts"]["hanging"]
    qb4 = [h for h in hanging if h["square"] == "b4"]
    assert len(qb4) == 1
    assert qb4[0]["piece"] == "Q"
    assert {a["square"] for a in qb4[0]["attackers"]} == {"f8"}
    assert qb4[0]["defenders"] == []
    assert "undefended" in qb4[0]["reason"]


@pytest.mark.asyncio
async def test_board_facts_absolute_pin_against_king(games_dir: Path) -> None:
    """Pin absolu : Rd5 blanc pin la dame noire d7 contre le roi noir d8."""
    fen = "3k4/3q4/8/3R4/8/8/8/3K4 w - - 0 1"
    body = _decode(await call_tool("chess_board_facts", {"fen": fen}))
    assert body["status"] == "ok"
    pins = body["facts"]["pins"]
    black_pins = [p for p in pins if p["pinned_color"] == "black"]
    assert len(black_pins) == 1
    assert black_pins[0]["pinned_square"] == "d7"
    assert black_pins[0]["pinned_piece"] == "q"
    assert "d8" in black_pins[0]["pin_line"]


@pytest.mark.asyncio
async def test_board_facts_move_san_string_returns_sequence_shape(
    games_dir: Path,
) -> None:
    """Uniform shape : single string ``move_san`` returns ``{sequence: [entry]}``."""
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    body = _decode(await call_tool("chess_board_facts", {"fen": fen, "move_san": "e4"}))
    assert body["status"] == "ok"
    seq = body["move_facts"]["sequence"]
    assert len(seq) == 1
    assert seq[0]["san"] == "e4"
    assert seq[0]["landed_square"] == "e4"
    assert "material" in seq[0]


@pytest.mark.asyncio
async def test_board_facts_move_san_sequence_replays(games_dir: Path) -> None:
    """List ``move_san`` : replays sequence, one entry per half-move,
    material_final at end."""
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    body = _decode(
        await call_tool(
            "chess_board_facts",
            {"fen": fen, "move_san": ["e4", "e5", "Nf3", "Nc6"]},
        )
    )
    assert body["status"] == "ok"
    seq = body["move_facts"]["sequence"]
    assert [e["san"] for e in seq] == ["e4", "e5", "Nf3", "Nc6"]
    # material_final should reflect starting position (no captures)
    mat = body["move_facts"]["material_final"]
    assert mat["white_total_cp"] == mat["black_total_cp"]  # even after developing


@pytest.mark.asyncio
async def test_board_facts_illegal_move_stops_sequence(games_dir: Path) -> None:
    """Illegal SAN in sequence → stopped_at + error, legal prefix preserved."""
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    body = _decode(
        await call_tool(
            "chess_board_facts",
            {"fen": fen, "move_san": ["e4", "e5", "ZZZ9"]},
        )
    )
    assert body["status"] == "ok"
    mf = body["move_facts"]
    assert mf["stopped_at"] == 2
    assert "error" in mf and "ZZZ9" in mf["error"]
    assert [e["san"] for e in mf["sequence"]] == ["e4", "e5"]
    assert "material_at_stop" in mf


@pytest.mark.asyncio
async def test_board_facts_rejects_both_fen_and_game_id(games_dir: Path) -> None:
    body = _decode(
        await call_tool(
            "chess_board_facts",
            {"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "game_id": "g"},
        )
    )
    assert body["status"] == "error"
    assert body["code"] == "INVALID_ARG"


@pytest.mark.asyncio
async def test_board_facts_rejects_missing_both(games_dir: Path) -> None:
    body = _decode(await call_tool("chess_board_facts", {}))
    assert body["status"] == "error"
    assert body["code"] == "INVALID_ARG"


@pytest.mark.asyncio
async def test_board_facts_checks_available_at_scholars_mate(games_dir: Path) -> None:
    """Position after 1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6 — White has Qxf7# and Qxh7."""
    fen = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    body = _decode(await call_tool("chess_board_facts", {"fen": fen}))
    assert body["status"] == "ok"
    checks = body["facts"]["checks_available"]
    # Qxf7# is a legal check (also mate but that's fine).
    assert "Qxf7#" in checks


@pytest.mark.asyncio
async def test_board_facts_expected_echoed_in_payload(games_dir: Path) -> None:
    """CD nit 1 — ``expected`` (free text) is echoed back beside the facts."""
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    prediction = "position de départ, rien en prise, rien de clouable"
    body = _decode(await call_tool("chess_board_facts", {"fen": fen, "expected": prediction}))
    assert body["status"] == "ok"
    assert body["expected"] == prediction


@pytest.mark.asyncio
async def test_board_facts_expected_without_game_id_no_journal(games_dir: Path) -> None:
    """Sans game_id (FEN seule), pas de journal — le fichier ne doit pas exister."""
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    body = _decode(
        await call_tool("chess_board_facts", {"fen": fen, "expected": "rien à signaler"})
    )
    assert body["status"] == "ok"
    assert body["expected"] == "rien à signaler"
    assert not (games_dir / "board_facts_log.jsonl").exists()


@pytest.mark.asyncio
async def test_board_facts_expected_with_game_id_writes_journal(games_dir: Path) -> None:
    """CD nit 2 — game_id + expected → ligne dans board_facts_log.jsonl."""
    import json as _json

    await call_tool("chess_create_game", {"game_id": "journal-1", "white": "w", "black": "b"})
    body = _decode(
        await call_tool(
            "chess_board_facts",
            {"game_id": "journal-1", "expected": "dame en e6 défendue"},
        )
    )
    assert body["status"] == "ok"
    log_path = games_dir / "board_facts_log.jsonl"
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    entry = _json.loads(lines[0])
    assert entry["game_id"] == "journal-1"
    assert entry["expected"] == "dame en e6 défendue"
    assert entry["side_to_move"] == "white"
    assert entry["ply"] == 0
    # Position de départ : rien en prise, rien de clouable
    assert entry["hanging_summary"] == []
    assert entry["pins_summary"] == []
    assert "ts" in entry


def test_journal_board_facts_gap_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit test — ``_journal_board_facts_gap`` mappe correctement les faits.

    Testé directement (pas via handler MCP) parce que ``chess_create_game``
    ne persiste pas le ``starting_fen`` dans le PGN (SetUp header absent),
    donc le handler ne peut pas ré-instancier une position custom via
    game_id. Le shape du journal est ce qui compte pour CD nit 2.
    """
    import chess as _chess

    from palamede_chessboard.mcp_chessboard_server import (
        _compute_board_facts,
        _journal_board_facts_gap,
    )

    log_path = tmp_path / "board_facts_log.jsonl"
    monkeypatch.setenv("OUTILLAGES_BOARD_FACTS_LOG", str(log_path))

    # Position from junior-vs-leader-002 ply31 — Qb4 hangs to Bf8.
    board = _chess.Board("r4bkr/pp3ppp/2p1p3/8/1Q6/2N2N2/PPP2PPP/R3K2R w KQ - 0 15")
    facts = _compute_board_facts(board)

    _journal_board_facts_gap("test-game", board, "Qb4 sûre", facts)

    assert log_path.exists()
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["game_id"] == "test-game"
    assert entry["expected"] == "Qb4 sûre"
    assert entry["side_to_move"] == "white"
    hanging_squares = [h["square"] for h in entry["hanging_summary"]]
    assert "b4" in hanging_squares


@pytest.mark.asyncio
async def test_board_facts_invalid_fen_returns_invalid_arg(games_dir: Path) -> None:
    """Admin nit — FEN invalide doit renvoyer INVALID_ARG, pas 500."""
    body = _decode(
        await call_tool("chess_board_facts", {"fen": "not-a-fen"}),
    )
    assert body["status"] == "error"
    assert body["code"] == "INVALID_ARG"
    assert "FEN invalide" in body["reason"]


@pytest.mark.asyncio
async def test_board_facts_hanging_lower_value_attacker_but_defended(games_dir: Path) -> None:
    """Admin nit 4 — branche isolée : queen défendue mais attaquée par
    pion (attaquant de moindre valeur) → hanging avec reason='attacked by
    lower-value piece' seulement (pas 'undefended')."""
    # White Qb4 défendue par Rb1, attaquée par pion noir en c5.
    fen = "4k3/8/8/2p5/1Q6/8/8/1R2K3 b - - 0 1"
    body = _decode(await call_tool("chess_board_facts", {"fen": fen}))
    assert body["status"] == "ok"
    hanging_b4 = [h for h in body["facts"]["hanging"] if h["square"] == "b4"]
    assert len(hanging_b4) == 1
    reason = hanging_b4[0]["reason"]
    assert "lower-value" in reason
    assert "undefended" not in reason


def test_journal_board_facts_gap_appends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deux appels → deux lignes JSONL (append, pas overwrite)."""
    import chess as _chess

    from palamede_chessboard.mcp_chessboard_server import (
        _compute_board_facts,
        _journal_board_facts_gap,
    )

    log_path = tmp_path / "board_facts_log.jsonl"
    monkeypatch.setenv("OUTILLAGES_BOARD_FACTS_LOG", str(log_path))

    board = _chess.Board()
    facts = _compute_board_facts(board)
    _journal_board_facts_gap("g1", board, "premier", facts)
    _journal_board_facts_gap("g1", board, "second", facts)

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["expected"] == "premier"
    assert json.loads(lines[1])["expected"] == "second"


def test_board_facts_log_path_default_is_per_user_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans override env, le journal vit sous ``~/.local/share/outillages/``.

    Vérifie le fix cross-user permission-denied : le journal doit être
    per-user, pas dans ``<games_dir>`` (owned par arbiter).
    """
    from palamede_chessboard.mcp_chessboard_server import _board_facts_log_path

    monkeypatch.delenv("OUTILLAGES_BOARD_FACTS_LOG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() reads $HOME on POSIX
    path = _board_facts_log_path()
    assert path == tmp_path / ".local" / "share" / "outillages" / "board_facts_log.jsonl"


def test_board_facts_log_path_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """L'env var ``OUTILLAGES_BOARD_FACTS_LOG`` prend le pas sur le défaut."""
    from palamede_chessboard.mcp_chessboard_server import _board_facts_log_path

    monkeypatch.setenv("OUTILLAGES_BOARD_FACTS_LOG", "/custom/path/journal.jsonl")
    assert _board_facts_log_path() == Path("/custom/path/journal.jsonl")


@pytest.mark.asyncio
async def test_board_facts_no_expected_no_journal(games_dir: Path) -> None:
    """Sans ``expected``, pas de journal même avec game_id."""
    await call_tool("chess_create_game", {"game_id": "no-expect", "white": "w", "black": "b"})
    body = _decode(await call_tool("chess_board_facts", {"game_id": "no-expect"}))
    assert body["status"] == "ok"
    assert "expected" not in body
    assert not (games_dir / "board_facts_log.jsonl").exists()


class TestListGamesTool:
    @pytest.mark.asyncio
    async def test_empty_dir(self, games_dir: Path) -> None:
        # Remove the arbiter pidfile side-effect — no game created.
        body = _decode(await call_tool("chess_list_games", {}))
        assert body["status"] == "ok"
        assert body["count"] == 0
        assert body["games"] == []

    @pytest.mark.asyncio
    async def test_returns_summaries(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "g1", "white": "junior", "black": "leader"},
        )
        await call_tool(
            "chess_create_game",
            {"game_id": "g2", "white": "admin", "black": "leader"},
        )
        body = _decode(await call_tool("chess_list_games", {}))
        assert body["status"] == "ok"
        assert body["count"] == 2
        ids = {g["game_id"] for g in body["games"]}
        assert ids == {"g1", "g2"}
        for g in body["games"]:
            assert set(g.keys()) == {
                "game_id",
                "white",
                "black",
                "result",
                "ply",
                "turn",
                "is_game_over",
                "mtime",
            }
            assert g["turn"] == "white"

    @pytest.mark.asyncio
    async def test_player_filter(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "jl", "white": "junior", "black": "leader"},
        )
        await call_tool(
            "chess_create_game",
            {"game_id": "ac", "white": "admin", "black": "cd"},
        )
        body = _decode(await call_tool("chess_list_games", {"player": "junior"}))
        assert body["count"] == 1
        assert body["games"][0]["game_id"] == "jl"

    @pytest.mark.asyncio
    async def test_active_only(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "a", "black": "b"},
        )
        await call_tool(
            "chess_create_game",
            {"game_id": "done", "white": "a", "black": "b"},
        )
        await call_tool("chess_resign", {"game_id": "done", "by": "white"})
        body = _decode(await call_tool("chess_list_games", {"active_only": True}))
        assert body["count"] == 1
        assert body["games"][0]["game_id"] == "live"


class TestGetEloTool:
    """``chess_get_elo`` : lecture + rebuild des ratings depuis les PGNs."""

    async def _create_server_game(
        self,
        *,
        game_id: str,
        white: str,
        black: str,
        resign_by: str,
    ) -> None:
        """Create a game then resign it so Site/Result qualify for Elo."""
        await call_tool(
            "chess_create_game",
            {
                "game_id": game_id,
                "white": white,
                "black": black,
                "site": "Chas, Tiresias",
            },
        )
        await call_tool("chess_resign", {"game_id": game_id, "by": resign_by})

    @pytest.mark.asyncio
    async def test_empty_dir_rebuilds_to_empty_board(self, games_dir: Path) -> None:
        body = _decode(await call_tool("chess_get_elo", {}))
        assert body["status"] == "ok"
        assert body["cache_hit"] is False
        assert body["provisional_games"] == 30
        assert body["count"] == 0
        assert body["players"] == []

    @pytest.mark.asyncio
    async def test_rebuild_produces_two_mirrored_updates(self, games_dir: Path) -> None:
        """Un match 1-0 : White gagne des points, Black en perd autant (même K)."""
        await self._create_server_game(
            game_id="g1", white="junior", black="leader", resign_by="black"
        )
        body = _decode(await call_tool("chess_get_elo", {}))
        assert body["status"] == "ok"
        assert body["count"] == 2
        names = {p["name"] for p in body["players"]}
        assert names == {"junior", "leader"}
        rating_by_name = {p["name"]: p for p in body["players"]}
        assert rating_by_name["junior"]["rating"] > 1500
        assert rating_by_name["leader"]["rating"] < 1500
        delta_white = rating_by_name["junior"]["rating"] - 1500
        delta_black = 1500 - rating_by_name["leader"]["rating"]
        assert delta_white == delta_black  # zero-sum, même K provisionnel

    @pytest.mark.asyncio
    async def test_provisional_flag_true_on_first_game(self, games_dir: Path) -> None:
        """Un seul match joué → les 2 joueurs sortent avec provisional=True."""
        await self._create_server_game(game_id="g1", white="a", black="b", resign_by="black")
        body = _decode(await call_tool("chess_get_elo", {}))
        for p in body["players"]:
            assert p["provisional"] is True
            assert p["games"] == 1

    @pytest.mark.asyncio
    async def test_provisional_flag_flips_at_threshold(self, games_dir: Path) -> None:
        """CD nit 2026-10-03 : vrai test de bascule provisional → established.

        Un joueur ``hero`` enchaîne PROVISIONAL_GAMES wins contre des ghosts
        distincts, le flag passe à False dès que ``games`` atteint
        ``PROVISIONAL_GAMES``.
        """
        from palamede_chessboard.chess_elo import PROVISIONAL_GAMES

        for i in range(PROVISIONAL_GAMES):
            await self._create_server_game(
                game_id=f"warmup-{i:02d}",
                white="hero",
                black=f"ghost-{i:02d}",
                resign_by="black",
            )
        body = _decode(await call_tool("chess_get_elo", {"player": "hero"}))
        hero = body["players"][0]
        assert hero["games"] == PROVISIONAL_GAMES
        assert hero["provisional"] is False

    @pytest.mark.asyncio
    async def test_player_filter_narrows_result(self, games_dir: Path) -> None:
        await self._create_server_game(
            game_id="g1", white="junior", black="leader", resign_by="black"
        )
        body = _decode(await call_tool("chess_get_elo", {"player": "junior"}))
        assert body["count"] == 1
        assert body["players"][0]["name"] == "junior"

    @pytest.mark.asyncio
    async def test_player_filter_unknown_returns_empty(self, games_dir: Path) -> None:
        await self._create_server_game(game_id="g1", white="a", black="b", resign_by="black")
        body = _decode(await call_tool("chess_get_elo", {"player": "ghost"}))
        assert body["count"] == 0
        assert body["players"] == []

    @pytest.mark.asyncio
    async def test_player_filter_normalised_match(self, games_dir: Path) -> None:
        """CD follow-up PR #282 2026-10-03 : filtre player passé par
        normalize_identity, donc Leader / LEADER / devleader doivent tous
        matcher la fiche canonique ``leader``."""
        await self._create_server_game(
            game_id="g1", white="junior", black="leader", resign_by="black"
        )
        for raw in ("Leader", "LEADER", " leader ", "devleader"):
            body = _decode(await call_tool("chess_get_elo", {"player": raw}))
            assert body["count"] == 1, f"{raw!r} did not match"
            assert body["players"][0]["name"] == "leader"

    @pytest.mark.asyncio
    async def test_include_history_returns_updates(self, games_dir: Path) -> None:
        await self._create_server_game(
            game_id="g1", white="junior", black="leader", resign_by="black"
        )
        body = _decode(await call_tool("chess_get_elo", {"include_history": True}))
        assert "history" in body
        assert len(body["history"]) == 1
        h = body["history"][0]
        assert h["game_id"] == "g1"
        assert h["white"] == "junior"
        assert h["black"] == "leader"
        assert h["white_score"] == 1.0
        assert h["white_delta"] > 0
        assert h["black_delta"] < 0

    @pytest.mark.asyncio
    async def test_default_omits_history(self, games_dir: Path) -> None:
        await self._create_server_game(game_id="g1", white="a", black="b", resign_by="black")
        body = _decode(await call_tool("chess_get_elo", {}))
        assert "history" not in body

    @pytest.mark.asyncio
    async def test_rebuild_sorted_by_rating_desc(self, games_dir: Path) -> None:
        """Winner ends up above loser dans la liste players."""
        await self._create_server_game(
            game_id="g1", white="winner", black="loser", resign_by="black"
        )
        body = _decode(await call_tool("chess_get_elo", {}))
        assert body["players"][0]["name"] == "winner"
        assert body["players"][1]["name"] == "loser"

    @pytest.mark.asyncio
    async def test_cache_only_missing_returns_null_board(self, games_dir: Path) -> None:
        """``cache_only=true`` sans cache préalable : réponse vide, pas d'erreur."""
        body = _decode(await call_tool("chess_get_elo", {"cache_only": True}))
        assert body["status"] == "ok"
        assert body["cache_hit"] is False
        assert body["count"] == 0

    @pytest.mark.asyncio
    async def test_rebuild_writes_cache_side_effect(self, games_dir: Path) -> None:
        """Un rebuild non-cache-only crée ``ratings.json`` dans ``games_dir``."""
        await self._create_server_game(
            game_id="g1", white="junior", black="leader", resign_by="black"
        )
        _ = _decode(await call_tool("chess_get_elo", {}))
        cache_path = games_dir / "ratings.json"
        assert cache_path.exists()
        # Second appel en cache_only doit maintenant hit.
        body = _decode(await call_tool("chess_get_elo", {"cache_only": True}))
        assert body["cache_hit"] is True
        assert body["count"] == 2

    @pytest.mark.asyncio
    async def test_imported_games_excluded_from_elo(self, games_dir: Path) -> None:
        """Un PGN sans header [Rated "true"] (import) ne doit pas peser."""
        await self._create_server_game(game_id="server", white="a", black="b", resign_by="black")
        # Simulate an imported PGN by writing one directly with foreign Site.
        imported = games_dir / "lichess-import.pgn"
        imported.write_text(
            '[Event "Lichess"]\n'
            '[Site "lichess.org"]\n'
            '[White "GM-A"]\n'
            '[Black "GM-B"]\n'
            '[Result "1-0"]\n\n1. e4 e5 1-0\n',
            encoding="utf-8",
        )
        body = _decode(await call_tool("chess_get_elo", {}))
        names = {p["name"] for p in body["players"]}
        assert names == {"a", "b"}
        assert "GM-A" not in names
        assert "GM-B" not in names


class TestEngineGuard:
    """Engine tools refuse to run on in-progress games and audit everything."""

    @pytest.mark.asyncio
    async def test_analyze_position_refused_by_game_id(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        body = _decode(
            await call_tool(
                "chess_analyze_position",
                {"game_id": "live", "depth": 5, "caller": "junior"},
            )
        )
        assert body["status"] == "error"
        assert body["code"] == "ENGINE_FORBIDDEN_INPROGRESS"
        assert body["matched_game_id"] == "live"
        log_path = games_dir / "engine_calls_log.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        assert entry["decision"] == "refused"
        assert entry["tool"] == "chess_analyze_position"
        assert entry["caller"] == "junior"

    @pytest.mark.asyncio
    async def test_analyze_position_refused_by_matching_fen(self, games_dir: Path) -> None:
        """FEN query against the current (post-opening) position of a live game."""
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        # Push 14 plies of Sicilian so the current position is past the
        # opening threshold (OPENING_PLIES_IGNORED = 12).
        for san in [
            "e4",
            "c5",
            "Nf3",
            "d6",
            "d4",
            "cxd4",
            "Nxd4",
            "Nf6",
            "Nc3",
            "a6",
            "Be3",
            "e5",
            "Nb3",
            "Be6",
        ]:
            await call_tool("chess_play_move", {"game_id": "live", "move_san": san})
        viewed = _decode(await call_tool("chess_view_state", {"game_id": "live"}))
        current_fen = viewed["state"]["fen"]
        body = _decode(
            await call_tool(
                "chess_analyze_position",
                {"fen": current_fen, "depth": 5},
            )
        )
        assert body["status"] == "error"
        assert body["code"] == "ENGINE_FORBIDDEN_INPROGRESS"
        assert body["matched_game_id"] == "live"

    @pytest.mark.asyncio
    async def test_analyze_position_refused_by_historical_fen(self, games_dir: Path) -> None:
        """Historical post-opening position : rewinding doesn't escape the guard."""
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        plies = [
            "e4",
            "c5",
            "Nf3",
            "d6",
            "d4",
            "cxd4",
            "Nxd4",
            "Nf6",
            "Nc3",
            "a6",
            "Be3",
            "e5",
            "Nb3",
            "Be6",
            "f3",
            "h6",
        ]
        for san in plies:
            await call_tool("chess_play_move", {"game_id": "live", "move_san": san})
        # Target : position at ply 14 (post-opening) — the live game has
        # advanced to ply 16 so this is a rewound historical position.
        import chess as _chess

        replay = _chess.Board()
        for san in plies[:14]:
            replay.push_san(san)
        body = _decode(
            await call_tool(
                "chess_analyze_position",
                {"fen": replay.fen(), "depth": 5},
            )
        )
        assert body["status"] == "error"
        assert body["code"] == "ENGINE_FORBIDDEN_INPROGRESS"
        assert body["matched_game_id"] == "live"

    @pytest.mark.asyncio
    async def test_analyze_position_allowed_on_opening_fen(self, games_dir: Path) -> None:
        """Opening positions (ply < threshold) stay allowed — shared theory."""
        from unittest.mock import MagicMock, patch

        import chess as _chess

        class _Score:
            def is_mate(self):
                return False

            def score(self):
                return 0

        class _Pov:
            def white(self):
                return _Score()

        engine = MagicMock()
        engine.analyse.return_value = {
            "score": _Pov(),
            "pv": [_chess.Move.from_uci("e2e4")],
            "depth": 5,
        }
        cm = MagicMock()
        cm.__enter__.return_value = engine
        cm.__exit__.return_value = False

        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        for san in ["e4", "c5", "Nf3", "d6"]:
            await call_tool("chess_play_move", {"game_id": "live", "move_san": san})
        # Opening position after 1.e4 — under the threshold, must be allowed.
        replay = _chess.Board()
        replay.push_san("e4")
        # #311 : patcher get_engine_path pour tourner en CI sans stockfish.
        with (
            patch(
                "palamede_chessboard.chessboard_engine.get_engine_path",
                return_value="/bin/true",
            ),
            patch(
                "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
                return_value=cm,
            ),
        ):
            body = _decode(
                await call_tool(
                    "chess_analyze_position",
                    {"fen": replay.fen(), "depth": 5},
                )
            )
        assert body["status"] == "ok"

    @pytest.mark.asyncio
    async def test_analyze_position_allowed_on_finished_game(self, games_dir: Path) -> None:
        from unittest.mock import MagicMock, patch

        import chess as _chess

        class _Score:
            def is_mate(self):
                return False

            def score(self):
                return 0

        class _Pov:
            def white(self):
                return _Score()

        engine = MagicMock()
        engine.analyse.return_value = {
            "score": _Pov(),
            "pv": [_chess.Move.from_uci("e2e4")],
            "depth": 5,
        }
        cm = MagicMock()
        cm.__enter__.return_value = engine
        cm.__exit__.return_value = False

        await call_tool(
            "chess_create_game",
            {"game_id": "done", "white": "junior", "black": "leader"},
        )
        await call_tool("chess_resign", {"game_id": "done", "by": "white"})
        # #311 : patcher get_engine_path pour tourner en CI sans stockfish.
        with (
            patch(
                "palamede_chessboard.chessboard_engine.get_engine_path",
                return_value="/bin/true",
            ),
            patch(
                "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci",
                return_value=cm,
            ),
        ):
            body = _decode(
                await call_tool(
                    "chess_analyze_position",
                    {"game_id": "done", "depth": 5},
                )
            )
        assert body["status"] == "ok"
        # The allowed call must still produce an audit entry.
        log_path = games_dir / "engine_calls_log.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        assert entry["decision"] == "allowed"
        assert entry["game_id"] == "done"

    @pytest.mark.asyncio
    async def test_analyze_game_refused_on_in_progress(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        body = _decode(await call_tool("chess_analyze_game", {"game_id": "live", "depth": 5}))
        assert body["status"] == "error"
        assert body["code"] == "ENGINE_FORBIDDEN_INPROGRESS"

    @pytest.mark.asyncio
    async def test_get_my_mistakes_refused_on_in_progress(self, games_dir: Path) -> None:
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        body = _decode(
            await call_tool(
                "chess_get_my_mistakes",
                {"game_id": "live", "player": "white", "depth": 5},
            )
        )
        assert body["status"] == "error"
        assert body["code"] == "ENGINE_FORBIDDEN_INPROGRESS"

    @pytest.mark.asyncio
    async def test_board_facts_still_works_on_in_progress(self, games_dir: Path) -> None:
        """``chess_board_facts`` is explicitly NOT guarded — stays usable."""
        await call_tool(
            "chess_create_game",
            {"game_id": "live", "white": "junior", "black": "leader"},
        )
        body = _decode(await call_tool("chess_board_facts", {"game_id": "live"}))
        assert body["status"] == "ok"
        assert "facts" in body
