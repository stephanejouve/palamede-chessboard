"""Shared helpers for the MCP chessboard server split.

Hosts the pure helpers and data constants that both the schema module and
the handler module need. See ``mcp_chessboard_server`` for the slim
transport entry point that re-exports these symbols for backwards
compatibility with existing tests.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import chess
from mcp.types import TextContent

from palamede_chessboard.chessboard_state import GAMES_DIR, ChessboardError

# --- games dir / arbiter contract -----------------------------------------

ARBITER_DAEMON_PIDFILE = ".arbiter-daemon.pid"


def _games_dir() -> Path:
    raw = os.environ.get("MCP_CHESSBOARD_GAMES_DIR", "").strip()
    return Path(raw) if raw else GAMES_DIR


def _arbiter_daemon_available(games_dir: Path) -> bool:
    """Return True iff a local arbiter daemon has claimed this games dir.

    Contract : the daemon writes its PID to ``<games_dir>/.arbiter-daemon.pid``
    on start and removes it on clean shutdown. A stale pidfile (process gone)
    is treated as no daemon available.
    """
    marker = games_dir / ARBITER_DAEMON_PIDFILE
    if not marker.exists():
        return False
    try:
        pid = int(marker.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Cross-user daemon : signal 0 refused but process exists.
        # CD bug report 2026-09-30 — treat as available.
        return True
    except OSError:
        return False
    return True


def _require_arbiter_or_daemon(arbiter: str | None, games_dir: Path) -> None:
    """Enforce game-creation contract : an arbiter must be identified."""
    if arbiter and arbiter.strip():
        return
    if _arbiter_daemon_available(games_dir):
        return
    raise ChessboardError(
        "Arbiter required : pass ``arbiter`` (IA agent id, e.g. "
        "``leader-bot``) at create time, or start the local arbiter daemon "
        f"(writes ``{ARBITER_DAEMON_PIDFILE}`` in the games dir)."
    )


# --- board facts (chess_board_facts tool) ---------------------------------

_PIECE_VALUES: dict[int, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

_PIECE_TYPE_NAMES: dict[int, str] = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}


def _square_list(bb: chess.SquareSet | int) -> list[str]:
    """Convert a SquareSet / bitboard into a sorted list of square names."""
    if isinstance(bb, int):
        bb = chess.SquareSet(bb)
    return sorted(chess.square_name(sq) for sq in bb)


def _attackers_with_symbols(board: chess.Board, color: bool, square: int) -> list[dict]:
    """Return list of ``{square, piece}`` for attackers of ``square`` by ``color``."""
    out = []
    for sq in board.attackers(color, square):
        piece = board.piece_at(sq)
        out.append(
            {
                "square": chess.square_name(sq),
                "piece": piece.symbol() if piece else "?",
            }
        )
    return sorted(out, key=lambda x: x["square"])


def _compute_pieces_and_hanging(board: chess.Board) -> tuple[list[dict], list[dict]]:
    """Return (pieces, hanging) lists per CD spec 2026-09-30.

    - pieces : per non-empty square, attackers + defenders as lists.
    - hanging : pieces attacked with no defender OR by strictly-lower-value piece.
    """
    pieces: list[dict] = []
    hanging: list[dict] = []
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is None:
            continue
        my_color = piece.color
        attackers = _attackers_with_symbols(board, not my_color, square)
        defenders = _attackers_with_symbols(board, my_color, square)
        pieces.append(
            {
                "square": chess.square_name(square),
                "piece": piece.symbol(),
                "color": "white" if my_color else "black",
                "type": _PIECE_TYPE_NAMES[piece.piece_type],
                "attackers": attackers,
                "defenders": defenders,
            }
        )
        if piece.piece_type == chess.KING or not attackers:
            continue
        my_value = _PIECE_VALUES[piece.piece_type]
        min_attacker_value = min(
            _PIECE_VALUES[board.piece_at(chess.parse_square(a["square"])).piece_type]  # type: ignore[union-attr]
            for a in attackers
        )
        undefended = not defenders
        lower_value_attacker = min_attacker_value < my_value
        if not (undefended or lower_value_attacker):
            continue
        reason_bits: list[str] = []
        if undefended:
            reason_bits.append("undefended")
        if lower_value_attacker:
            reason_bits.append(
                f"attacked by lower-value piece (min={min_attacker_value}, self={my_value})"
            )
        hanging.append(
            {
                "square": chess.square_name(square),
                "piece": piece.symbol(),
                "color": "white" if my_color else "black",
                "attackers": attackers,
                "defenders": defenders,
                "reason": ", ".join(reason_bits),
            }
        )
    return pieces, hanging


def _compute_pins(board: chess.Board) -> list[dict]:
    """Return absolute pins (against the king) for both colors.

    python-chess exposes king-pins only via is_pinned/pin ; relative pins
    (e.g. pinned against the queen) are out of scope for v1.
    """
    pins: list[dict] = []
    for color, color_name in ((chess.WHITE, "white"), (chess.BLACK, "black")):
        for square in chess.SQUARES:
            piece = board.piece_at(square)
            if piece is None or piece.color != color:
                continue
            if not board.is_pinned(color, square):
                continue
            pin_line = board.pin(color, square)
            pins.append(
                {
                    "pinned_square": chess.square_name(square),
                    "pinned_piece": piece.symbol(),
                    "pinned_color": color_name,
                    "pin_line": _square_list(pin_line),
                }
            )
    return pins


def _compute_board_facts(board: chess.Board) -> dict:
    """Verifiable facts about ``board`` — no evaluation, no engine.

    See CD spec 2026-09-30. NEVER returns score / best move / eval.
    """
    pieces, hanging = _compute_pieces_and_hanging(board)
    pins = _compute_pins(board)
    checks_available = sorted(
        board.san(move) for move in board.legal_moves if board.gives_check(move)
    )
    return {
        "fen": board.fen(),
        "turn": "white" if board.turn else "black",
        "pieces": pieces,
        "hanging": hanging,
        "pins": pins,
        "checks_available": checks_available,
        "xray_note": (
            "board.attackers() ne voit pas les rayons X. Une pièce alignée "
            "derrière une autre sur la même ligne (batterie dame-tour p.ex.) "
            "n'apparaît PAS dans attackers/defenders — ne pas utiliser ces "
            "listes comme entrée SEE."
        ),
    }


def _board_facts_log_path() -> Path:
    """Return the per-user log path for board-facts predictions."""
    override = os.environ.get("OUTILLAGES_BOARD_FACTS_LOG", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "outillages" / "board_facts_log.jsonl"


def _journal_board_facts_gap(
    game_id: str,
    board: chess.Board,
    expected: str,
    facts: dict,
) -> None:
    """Append one line to the per-user board_facts_log.jsonl.

    CD spec 2026-09-30 nit 2 : logue prédiction + résumé des faits (pièces
    en prise, clouages). Pas de verdict automatique.
    """
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "game_id": game_id,
        "ply": board.ply(),
        "side_to_move": "white" if board.turn else "black",
        "expected": expected,
        "hanging_summary": [{"square": h["square"], "piece": h["piece"]} for h in facts["hanging"]],
        "pins_summary": [
            {"pinned_square": p["pinned_square"], "pinned_piece": p["pinned_piece"]}
            for p in facts["pins"]
        ],
    }
    log_path = _board_facts_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _material_summary(board: chess.Board) -> dict:
    """Count pieces + centipawn total per color. Kings excluded."""
    counts: dict[str, dict[str, int]] = {"white": {}, "black": {}}
    total = {"white": 0, "black": 0}
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is None or piece.piece_type == chess.KING:
            continue
        color = "white" if piece.color else "black"
        name = _PIECE_TYPE_NAMES[piece.piece_type]
        counts[color][name] = counts[color].get(name, 0) + 1
        total[color] += _PIECE_VALUES[piece.piece_type]
    return {
        "white_pieces": counts["white"],
        "black_pieces": counts["black"],
        "white_total_cp": total["white"],
        "black_total_cp": total["black"],
        "balance_cp": total["white"] - total["black"],
    }


def _single_move_facts(board: chess.Board, move_san: str, pre_facts: dict) -> dict:
    """Simulate one SAN on ``board`` (mutates it) and return per-move facts."""
    move = board.parse_san(move_san)
    landed_square = move.to_square
    board.push(move)

    landed_piece = board.piece_at(landed_square)
    my_color = landed_piece.color if landed_piece else not board.turn
    landed_attackers = _attackers_with_symbols(board, not my_color, landed_square)
    landed_defenders = _attackers_with_symbols(board, my_color, landed_square)

    post_facts = _compute_board_facts(board)
    pre_hanging_squares = {h["square"] for h in pre_facts["hanging"]}
    newly_hanging = [h for h in post_facts["hanging"] if h["square"] not in pre_hanging_squares]

    return {
        "san": move_san,
        "landed_square": chess.square_name(landed_square),
        "landed_piece": landed_piece.symbol() if landed_piece else None,
        "landed_attackers": landed_attackers,
        "landed_defenders": landed_defenders,
        "newly_hanging": newly_hanging,
        "post_move_facts": post_facts,
        "material": _material_summary(board),
    }


def _compute_move_facts(board: chess.Board, move_san: str | list[str]) -> dict:
    """Simulate ``move_san`` (single SAN or ordered list) on a COPY of the
    board, returning facts after each half-move.
    """
    if isinstance(move_san, str):
        moves = [move_san]
    else:
        moves = list(move_san)

    board_copy = board.copy(stack=False)
    sequence: list[dict] = []
    pre_facts = _compute_board_facts(board_copy)

    for idx, san in enumerate(moves):
        try:
            entry = _single_move_facts(board_copy, san, pre_facts)
        except (chess.IllegalMoveError, chess.InvalidMoveError, chess.AmbiguousMoveError) as exc:
            return {
                "sequence": sequence,
                "stopped_at": idx,
                "error": f"move ``{san}`` illegal on position after {idx} previous move(s) : {exc}",
                "material_at_stop": _material_summary(board_copy),
            }
        sequence.append(entry)
        pre_facts = entry["post_move_facts"]

    return {
        "sequence": sequence,
        "material_final": _material_summary(board_copy),
    }


def _mcp_response(result: dict[str, Any]) -> list[TextContent]:
    result["_metadata"] = {
        "response_date": date.today().isoformat(),
        "response_timestamp": datetime.now().isoformat(),
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
