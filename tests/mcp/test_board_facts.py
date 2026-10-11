"""Tests for `palamede_chessboard.mcp_chessboard_server` — chess_board_facts (pins / hanging / sequences / journal).

Carved out of the pre-split 1471l test_mcp_chessboard_server.py.
Shared `games_dir` fixture lives in tests/mcp/conftest.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode

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
