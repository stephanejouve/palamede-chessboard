"""MCP ChessBoard Server — skeleton for server-side validated chess games.

Exposes a thin MCP wrapper around :mod:`palamede_chessboard.chessboard_state`. Goal
post Round-2 Admin-vs-Leader debrief : move validation lives **server-side**
so illegal SAN, ambiguous moves, missing disambiguation, blocked pieces and
self-pinned king moves are rejected by ``chess.Board.parse_san`` before they
contaminate the PGN.

Status : **skeleton**. Tools are wired and persistence works ; transport is
stdio-only ; no per-game turn enforcement ; no clock ; no spectator-only
broadcast channel. T-mcp-chessboard-server tracks the remaining work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import chess
from mcp.server import Server
from mcp.types import TextContent, Tool

from palamede_chessboard.chess_elo import (
    PROVISIONAL_GAMES,
    load_cached_ratings,
    normalize_identity,
    rebuild_and_persist_cache,
)
from palamede_chessboard.chess_engine_guard import (
    check_engine_call,
    log_engine_call,
    mask_walk_entries,
)
from palamede_chessboard.chessboard_clock import (
    init_and_save_clock,
    load_clock,
    result_from_flag_fall,
    save_clock,
)
from palamede_chessboard.chessboard_engine import (
    DEFAULT_DEPTH,
    EngineUnavailableError,
    analyze_game,
    analyze_position,
    get_my_mistakes,
)
from palamede_chessboard.chessboard_state import (
    GAMES_DIR,
    ChessboardError,
    GameAlreadyExistsError,
    GameMetadata,
    GameNotFoundError,
    GameState,
    IllegalMoveError,
    InvalidGameStateError,
    StalePlyError,
    TimeControl,
    get_commentary,
    list_games,
    post_commentary,
)

logger = logging.getLogger(__name__)

server = Server("mcp-chessboard")


def _games_dir() -> Path:
    raw = os.environ.get("MCP_CHESSBOARD_GAMES_DIR", "").strip()
    return Path(raw) if raw else GAMES_DIR


# --- arbiter contract ------------------------------------------------------

ARBITER_DAEMON_PIDFILE = ".arbiter-daemon.pid"


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
        # No process with this PID → stale pidfile.
        return False
    except PermissionError:
        # PID exists but owned by a different user (POSIX : signal 0 is a
        # permission-checked no-op that succeeds only when the caller can
        # signal the target). Cross-user setup is the normal case here :
        # the daemon runs as ``magma`` while MCP servers spawn under
        # ``stephanejouve`` (Leader, Claude Desktop) or ``admin``. The
        # daemon IS alive — treat as available. CD bug report 2026-09-30.
        return True
    except OSError:
        # Other errno (rare — EINVAL on invalid signal, etc.) : treat
        # defensively as unavailable rather than crash the tool call.
        return False
    return True


# --- board facts (chess_board_facts tool) ---------------------------------

# Nominal centipawn value per piece type. Kings excluded (can't be captured).
# Used for the "attacked by lower-value piece" heuristic — matches CD spec
# 2026-09-30 : « attaquées par une piece de moindre valeur ».
_PIECE_VALUES: dict[int, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,  # priceless — never counted for hanging
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
    """Return list of ``{square, piece}`` for attackers of ``square`` by ``color``.

    Wraps ``board.attackers()`` which returns raw squares — we enrich with the
    piece symbol so the caller can tell at a glance if the attacker is a pawn
    vs a queen (matters for the "lower-value attacker" heuristic).
    """
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


def _compute_board_facts(board: chess.Board) -> dict:
    """Verifiable facts about ``board`` — no evaluation, no engine.

    Contract (CD spec 2026-09-30) :

    - ``pieces`` : per non-empty square, attackers (opposite color) and
      defenders (same color) as lists of {square, piece}, not counts.
    - ``hanging`` : pieces attacked with no defender OR attacked by a
      strictly-lower-value piece.
    - ``pins`` : absolute pins against the king (via ``board.is_pinned`` +
      ``board.pin``). Relative pins (against a queen etc.) are NOT included
      — python-chess only exposes absolute pins.
    - ``checks_available`` : all legal moves for the side-to-move that give
      check, as SAN.
    - ``xray_note`` : docstring reminder — ``board.attackers()`` does NOT
      see through pieces (no batteries / X-ray). A queen behind a rook on
      the same file is not listed as an attacker of the file's target
      square. Callers must not treat the attackers count as SEE input.

    NEVER returns : score, best move, evaluation, ranking, engine output.
    """
    pieces = []
    hanging = []

    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is None:
            continue

        my_color = piece.color
        # Attackers = pieces of opposite color that attack this square.
        # Defenders = pieces of same color that would recapture on this square.
        attackers = _attackers_with_symbols(board, not my_color, square)
        defenders = _attackers_with_symbols(board, my_color, square)

        entry = {
            "square": chess.square_name(square),
            "piece": piece.symbol(),
            "color": "white" if my_color else "black",
            "type": _PIECE_TYPE_NAMES[piece.piece_type],
            "attackers": attackers,
            "defenders": defenders,
        }
        pieces.append(entry)

        # Hanging test — kings never counted (can't be captured).
        if piece.piece_type == chess.KING:
            continue
        if not attackers:
            continue

        my_value = _PIECE_VALUES[piece.piece_type]
        min_attacker_value = min(
            _PIECE_VALUES[board.piece_at(chess.parse_square(a["square"])).piece_type]  # type: ignore[union-attr]
            for a in attackers
        )
        undefended = not defenders
        lower_value_attacker = min_attacker_value < my_value

        if undefended or lower_value_attacker:
            reason_bits = []
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

    # Absolute pins (against the king) for both colors — python-chess only
    # exposes king-pins via is_pinned/pin. Relative pins (e.g. pinned against
    # the queen) require custom sliding-piece logic ; out of scope for v1.
    pins = []
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

    # Legal moves that give check, in SAN — for the side-to-move only.
    # ``board.gives_check(move)`` is the pure-function form ; no push/pop
    # dance, no in-place mutation. SAN can then be generated on the
    # unmodified pre-move board.
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
    """Return the per-user log path for board-facts predictions.

    Le journal est une réflexion PERSONNELLE (ma prédiction vs les faits),
    pas un log partagé — donc il vit sous le home de l'user qui fait tourner
    ce MCP server, pas dans ``<games_dir>`` (qui est owned par ``arbiter``
    et non-writable par les autres joueurs, cross-user permission denied).

    Override via env var ``OUTILLAGES_BOARD_FACTS_LOG`` pour tests / setups
    custom.
    """
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
    """Append one line au journal per-user ``~/.local/share/outillages/board_facts_log.jsonl``.

    CD spec 2026-09-30 nit 2 : logue prédiction + résumé des faits (pièces en
    prise, clouages). Aucun verdict automatique — c'est le joueur qui juge
    l'écart. Le journal donne la mesure : le taux d'écarts par partie devrait
    baisser si les leçons servent.

    Chaque joueur (magma, admin, stephanejouve, leader, arbiter) a son
    propre journal sous son home — pas de partage cross-user, pas de race
    condition entre MCP servers concurrents.
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
    """Count pieces + centipawn total per color. Kings excluded (constant)."""
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
    """Simulate one SAN on ``board`` (mutates ``board``) and return facts
    on the landing square + newly-hanging + full post-move facts + material.

    ``pre_facts`` is the facts BEFORE this move — used to compute the
    "newly hanging" diff. Caller must pass consistent pre_facts.
    """
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

    - Single SAN → returns ``{sequence: [<one entry>]}`` for uniform shape.
    - List of SAN → returns ``{sequence: [<entry per move>]}``, stopping at
      the first illegal move with ``stopped_at`` + ``error`` fields (facts
      up to and including the last legal move are still returned).

    Contract CD 2026-09-30 : « échiquier d'analyse, pas un moteur ». No
    evaluation, no suggestion — only verifiable facts per position.
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


def _require_arbiter_or_daemon(arbiter: str | None, games_dir: Path) -> None:
    """Enforce game-creation contract : an arbiter must be identified.

    Either an IA agent is designated via ``arbiter`` (any non-empty string),
    or a local daemon has published its pidfile in ``games_dir``. Games
    created before this contract existed remain readable via ``chess_view_state``
    — the check runs only at ``chess_create_game`` time.
    """
    if arbiter and arbiter.strip():
        return
    if _arbiter_daemon_available(games_dir):
        return
    raise ChessboardError(
        "Arbiter required : pass ``arbiter`` (IA agent id, e.g. "
        "``leader-bot``) at create time, or start the local arbiter daemon "
        f"(writes ``{ARBITER_DAEMON_PIDFILE}`` in the games dir)."
    )


def _mcp_response(result: dict[str, Any]) -> list[TextContent]:
    result["_metadata"] = {
        "response_date": date.today().isoformat(),
        "response_timestamp": datetime.now().isoformat(),
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="chess_create_game",
            description=(
                "Create a new game with player/arbiter/time-control schema. "
                "Returns the initial state. Fails if game_id already exists. "
                "Requires an arbiter : either the ``arbiter`` field is set to "
                "an IA agent id, or a local arbiter daemon must have published "
                "its pidfile in the games dir."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {
                        "type": "string",
                        "description": "Unique short id (^[a-zA-Z0-9_.-]{1,64}$)",
                    },
                    "white": {"type": "string"},
                    "black": {"type": "string"},
                    "arbiter": {
                        "type": "string",
                        "description": (
                            "IA agent id responsible for arbitration (any "
                            "non-empty string). If omitted, a running local "
                            "arbiter daemon must have claimed the games dir."
                        ),
                    },
                    "spectators": {"type": "array", "items": {"type": "string"}},
                    "event": {"type": "string"},
                    "site": {"type": "string"},
                    "round": {"type": "string", "default": "1"},
                    "time_control": {
                        "type": "object",
                        "properties": {
                            "format": {"type": "string"},
                            "start_seconds": {"type": "integer"},
                            "increment_seconds": {"type": "integer"},
                        },
                    },
                    "annotator": {"type": "string"},
                    "opening": {"type": "string"},
                    "eco": {"type": "string"},
                    "starting_fen": {"type": "string", "description": "Optional FEN override"},
                },
                "required": ["game_id", "white", "black"],
            },
        ),
        Tool(
            name="chess_view_state",
            description="Read-only snapshot of a game (metadata, FEN, ply, history, result).",
            inputSchema={
                "type": "object",
                "properties": {"game_id": {"type": "string"}},
                "required": ["game_id"],
            },
        ),
        Tool(
            name="chess_list_games",
            description=(
                "Enumerate games available on the shared board. Returns a "
                "list of summaries (game_id, white, black, result, ply, "
                "turn, is_game_over, mtime) sorted by mtime desc. Use "
                "``player`` to filter to one agent's games (matches "
                "white OR black, case-insensitive). Use "
                "``active_only=true`` to drop finished games. Malformed "
                "PGN files are skipped. Lets a caller discover its "
                "game_id without touching the filesystem."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "player": {
                        "type": "string",
                        "description": (
                            "Keep only games where white or black matches "
                            "this identifier (case-insensitive)."
                        ),
                    },
                    "active_only": {
                        "type": "boolean",
                        "default": False,
                        "description": "Keep only games whose result is still ``*``.",
                    },
                },
            },
        ),
        Tool(
            name="chess_get_elo",
            description=(
                "Return current Elo ratings computed from the PGNs on the "
                "shared board. By default rebuilds from scratch (source "
                "of truth = PGN headers), then refreshes the "
                "``ratings.json`` cache side-car. Pass ``cache_only=true`` "
                "for a read-only lookup that returns ``null`` if the cache "
                "is missing. Pass ``player`` to narrow the response to one "
                "entry. Pass ``include_history=true`` to get the per-game "
                "update trail (useful for post-mortem). K-factor : 40 "
                f"provisional for the first {PROVISIONAL_GAMES} games, "
                "then 20 (FIDE standard)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "player": {
                        "type": "string",
                        "description": (
                            "Narrow the response to one player's rating. "
                            "Normalised via ``normalize_identity`` (strip "
                            "+ lower + alias table), so ``Leader`` / "
                            "``devleader`` / ``LEADER`` all map to "
                            "``leader``."
                        ),
                    },
                    "cache_only": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Skip the rebuild and read ``ratings.json`` as-"
                            "is. Returns ``cache_hit=false`` + empty "
                            "``players`` if the cache is missing."
                        ),
                    },
                    "include_history": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Include the ``history`` list (per-game Elo "
                            "deltas with k-factor and before/after)."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="chess_play_move",
            description=(
                "Validate a SAN move against the live board and persist the PGN. "
                "Illegal/ambiguous SAN is rejected. Returns the new state. "
                "If ``expected_ply`` is supplied and the board has already "
                "advanced past it (concurrent writer), the call is rejected "
                "with ``code=STALE_PLY`` BEFORE the move is applied — the "
                "caller should re-read state and retry."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                    "move_san": {"type": "string", "description": "e.g. e4, Nf3, exd5, O-O, e8=Q+"},
                    "by": {"type": "string", "description": "Player identifier (audit only)"},
                    "expected_ply": {
                        "type": "integer",
                        "minimum": 0,
                        "description": (
                            "Idempotence guard. If provided and ≠ current "
                            "board.ply(), returns STALE_PLY without pushing."
                        ),
                    },
                },
                "required": ["game_id", "move_san"],
            },
        ),
        Tool(
            name="chess_resign",
            description=(
                "Resign the game on behalf of one side. Updates result "
                "(0-1 if white resigns, 1-0 if black resigns), writes a "
                "termination note in the PGN, and clears any pending draw "
                "offer. Refused if the game is already over."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                    "by": {
                        "type": "string",
                        "description": "'white' | 'black' or player name matching White/Black",
                    },
                },
                "required": ["game_id", "by"],
            },
        ),
        Tool(
            name="chess_offer_draw",
            description=(
                "Register a draw offer from one side, awaiting the other "
                "side's response (agree_draw or decline_draw). Idempotent "
                "when re-offering from the same side. Refused if the "
                "opposite side already has a pending offer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                    "by": {
                        "type": "string",
                        "description": "'white' | 'black' or player name matching White/Black",
                    },
                },
                "required": ["game_id", "by"],
            },
        ),
        Tool(
            name="chess_agree_draw",
            description=(
                "Accept a pending draw offer from the opposite side, "
                "ending the game with result 1/2-1/2. Refused if no "
                "offer is pending, or if the accepting side made the offer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                    "by": {
                        "type": "string",
                        "description": "'white' | 'black' or player name matching White/Black",
                    },
                },
                "required": ["game_id", "by"],
            },
        ),
        Tool(
            name="chess_decline_draw",
            description=(
                "Decline a pending draw offer from the opposite side. "
                "Clears the offer without ending the game. Playing a "
                "move implicitly declines too (see chess_play_move)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                    "by": {
                        "type": "string",
                        "description": "'white' | 'black' or player name matching White/Black",
                    },
                },
                "required": ["game_id", "by"],
            },
        ),
        Tool(
            name="chess_get_raw_pgn",
            description="Return the raw PGN file content for a game.",
            inputSchema={
                "type": "object",
                "properties": {"game_id": {"type": "string"}},
                "required": ["game_id"],
            },
        ),
        Tool(
            name="chess_post_commentary",
            description="Append a ply-indexed commentary line to the per-game side-car log.",
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                    "ply": {"type": "integer", "minimum": 0},
                    "source": {"type": "string", "description": "Free-form id (e.g. arbiter name)"},
                    "text": {"type": "string"},
                },
                "required": ["game_id", "ply", "source", "text"],
            },
        ),
        Tool(
            name="chess_get_commentary",
            description="Return commentary entries for a game (all or filtered by ply).",
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                    "ply": {"type": "integer", "minimum": 0},
                },
                "required": ["game_id"],
            },
        ),
        Tool(
            name="chess_clock_state",
            description=(
                "Return the clock snapshot for a game "
                "(white/black seconds, active side, flag_fall flag)."
            ),
            inputSchema={
                "type": "object",
                "properties": {"game_id": {"type": "string"}},
                "required": ["game_id"],
            },
        ),
        Tool(
            name="chess_clock_start",
            description=(
                "Start the clock for a game (active=white, last_move_ts=now). "
                "No-op if the game has no clock (TimeControl=unrated)."
            ),
            inputSchema={
                "type": "object",
                "properties": {"game_id": {"type": "string"}},
                "required": ["game_id"],
            },
        ),
        Tool(
            name="chess_board_facts",
            description=(
                "Faits vérifiables sur une position d'échecs — SANS évaluation. "
                "\n\n"
                "**Usage recommandé en trois temps (CD 2026-09-30)** :\n"
                "1. **Prédis** — écris ta lecture dans ``expected`` avant "
                "l'appel (ex : « ma dame en e6 est défendue, rien ne la "
                "menace »). L'outil ne l'interprète pas, il te la renvoie "
                "telle quelle à côté des faits.\n"
                "2. **Vérifie** — compare ta prédiction aux faits retournés. "
                "Si un écart saute aux yeux (pièce en prise que tu croyais "
                "défendue, clouage manqué…), c'est une leçon.\n"
                "3. **Note la leçon dans ta mémoire** — l'outil produit des "
                "leçons, il ne les remplace pas. Nos poids ne bougent pas "
                "d'une partie à l'autre : ce qui s'accumule c'est la mémoire "
                "écrite. Le journal ``board_facts_log.jsonl`` donne la "
                "mesure : le taux d'écarts par partie devrait baisser si les "
                "leçons servent.\n"
                "\n"
                "**Contenu retourné** : pour chaque pièce, case + attaquants "
                "et défenseurs (listes de {square, piece}, pas des "
                "compteurs). Pièces en prise : attaquées et non-défendues OU "
                "attaquées par une pièce de moindre valeur. Clouages absolus "
                "(contre le roi). Coups d'échec disponibles pour le camp au "
                "trait, en SAN. Avec ``move_san`` : simule le(s) coup(s) sur "
                "une COPIE du board — case d'arrivée, pièces nouvellement en "
                "prise (y compris par découverte). "
                "\n\n"
                "**Note ray-X** : ``board.attackers()`` ne voit pas les "
                "pièces alignées derrière (batteries) — les attackers listés "
                "ne sont pas une entrée SEE. "
                "\n\n"
                "**Interdit** : score, meilleur coup, ranking, variante, "
                "Stockfish."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {
                        "type": "string",
                        "description": "Charge la position courante du game (exclusif avec fen)",
                    },
                    "fen": {
                        "type": "string",
                        "description": "FEN libre (exclusif avec game_id)",
                    },
                    "move_san": {
                        "description": (
                            "Optionnel — string OU array de SAN. Simule le(s) "
                            "coup(s) sur une COPIE du board (le game n'est PAS "
                            "modifié), et renvoie les faits après chaque "
                            "demi-coup : case d'arrivée, pièces nouvellement "
                            "en prise, faits complets post-move, matériel. "
                            "Séquence stoppée au 1er coup illégal avec "
                            "``stopped_at`` + ``error``, les coups légaux "
                            "précédents sont conservés dans ``sequence``."
                        ),
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                    },
                    "expected": {
                        "type": "string",
                        "description": (
                            "Optionnel — ta prédiction en texte libre AVANT "
                            "l'appel (ex : « dame en e6 défendue, rien ne la "
                            "menace »). L'outil ne l'interprète pas : il te "
                            "la renvoie telle quelle dans la réponse pour que "
                            "l'écart avec les faits saute aux yeux. Si "
                            "``game_id`` est fourni, une ligne est ajoutée à "
                            "``~/.local/share/outillages/board_facts_log.jsonl`` "
                            "(journal per-user sous le home du process MCP — "
                            "chacun le sien, pas de partage cross-user) : ply, "
                            "side_to_move, expected, résumé pièces en "
                            "prise/clouages. Pas de verdict automatique, "
                            "c'est toi qui juges l'écart. Override via env "
                            "``OUTILLAGES_BOARD_FACTS_LOG``."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="chess_analyze_position",
            description=(
                "Run local Stockfish on a position (game current FEN, or a "
                "specific ply, or a free-form FEN). Returns score in cp, "
                "best move, and principal variation. Refused on positions "
                "that belong to an in-progress game — call is logged in "
                "``~/.local/share/outillages/engine_calls_log.jsonl`` "
                "either way."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {"type": "string", "description": "Optional if fen provided"},
                    "ply": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Optional position selector",
                    },
                    "fen": {"type": "string", "description": "Optional FEN override"},
                    "depth": {"type": "integer", "minimum": 1, "default": DEFAULT_DEPTH},
                    "caller": {
                        "type": "string",
                        "description": "Agent identifier for the audit log (optional).",
                    },
                },
            },
        ),
        Tool(
            name="chess_analyze_game",
            description=(
                "Walk every ply of a game, evaluate before/after, classify "
                "each move as blunder/mistake/inaccuracy/good. Refused on "
                "in-progress games — audit entry written in all cases."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 1, "default": DEFAULT_DEPTH},
                    "caller": {
                        "type": "string",
                        "description": "Agent identifier for the audit log (optional).",
                    },
                },
                "required": ["game_id"],
            },
        ),
        Tool(
            name="chess_get_my_mistakes",
            description=(
                "Return the subset of one player's moves classified above "
                "min_classification (default inaccuracy). Refused on "
                "in-progress games — audit entry written in all cases."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                    "player": {"type": "string", "enum": ["white", "black"]},
                    "min_classification": {
                        "type": "string",
                        "enum": ["inaccuracy", "mistake", "blunder"],
                        "default": "inaccuracy",
                    },
                    "depth": {"type": "integer", "minimum": 1, "default": DEFAULT_DEPTH},
                    "caller": {
                        "type": "string",
                        "description": "Agent identifier for the audit log (optional).",
                    },
                },
                "required": ["game_id", "player"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    games_dir = _games_dir()
    try:
        if name == "chess_create_game":
            _require_arbiter_or_daemon(arguments.get("arbiter"), games_dir)
            tc_raw = arguments.get("time_control") or {}
            tc = TimeControl(
                format=tc_raw.get("format", "unrated"),
                start_seconds=tc_raw.get("start_seconds"),
                increment_seconds=tc_raw.get("increment_seconds"),
            )
            metadata = GameMetadata(
                game_id=arguments["game_id"],
                event=arguments.get("event", ""),
                site=arguments.get("site", ""),
                round=arguments.get("round", "1"),
                white=arguments["white"],
                black=arguments["black"],
                arbiter=arguments.get("arbiter") or None,
                spectators=list(arguments.get("spectators") or []),
                time_control=tc,
                annotator=arguments.get("annotator") or None,
                opening=arguments.get("opening") or None,
                eco=arguments.get("eco") or None,
            )
            state = GameState.create(
                metadata,
                games_dir=games_dir,
                starting_fen=arguments.get("starting_fen") or None,
            )
            # CD review PR #297 bloquant 2 : factored helper so the
            # CLI chess-matchmaking create uses the exact same init +
            # save sequence (no clock drift between MCP and CLI).
            clock = init_and_save_clock(state.metadata.game_id, tc, games_dir=games_dir)
            return _mcp_response(
                {
                    "status": "ok",
                    "state": state.to_state_dict(),
                    "clock": clock.to_dict() if clock is not None else None,
                }
            )

        if name == "chess_view_state":
            state = GameState.load(arguments["game_id"], games_dir=games_dir)
            return _mcp_response({"status": "ok", "state": state.to_state_dict()})

        if name == "chess_list_games":
            games = list_games(
                games_dir,
                player=arguments.get("player"),
                active_only=bool(arguments.get("active_only", False)),
            )
            return _mcp_response(
                {
                    "status": "ok",
                    "count": len(games),
                    "games": games,
                }
            )

        if name == "chess_get_elo":
            # Normalise the player filter via the same identity rules the
            # RatingBoard keys use (strip, lowercase, alias). Without this
            # a caller passing "Leader" / "devleader" / " admin " would get
            # an empty response even though the player is in the board.
            # CD follow-up PR #282 2026-10-03.
            raw_player = arguments.get("player")
            player_filter = normalize_identity(raw_player) if raw_player else None
            include_history = bool(arguments.get("include_history", False))
            cache_only = bool(arguments.get("cache_only", False))

            if cache_only:
                board = load_cached_ratings(games_dir=games_dir)
                cache_hit = board is not None
            else:
                # CD review 2026-10-05 PR #295 (cycle 3 non-blocking) :
                # route via rebuild_and_persist_cache so a subsequent
                # /api/elo poll from the viewer finds the cache fresh
                # (same source_mtime + source_pgns snapshot). Previous
                # path called rebuild_ratings_from_games_dir() then
                # save_cached_ratings() without the pre-rebuild snapshot,
                # which left source_mtime=None → viewer treated as stale
                # → one extra rebuild per MCP call.
                board, _ = rebuild_and_persist_cache(games_dir=games_dir)
                cache_hit = False

            players_payload: list[dict[str, Any]] = []
            if board is not None:
                items = sorted(
                    board.players.items(),
                    key=lambda kv: (-kv[1].rating, kv[0]),
                )
                for name_, rating in items:
                    if player_filter and name_ != player_filter:
                        continue
                    players_payload.append(
                        {
                            "name": name_,
                            "rating": rating.rating,
                            "games": rating.games,
                            "provisional": rating.games < PROVISIONAL_GAMES,
                        }
                    )

            payload: dict[str, Any] = {
                "status": "ok",
                "cache_hit": cache_hit,
                "provisional_games": PROVISIONAL_GAMES,
                "count": len(players_payload),
                "players": players_payload,
            }
            if include_history and board is not None:
                payload["history"] = [u.to_dict() for u in board.history]
            return _mcp_response(payload)

        if name == "chess_play_move":
            state = GameState.load(arguments["game_id"], games_dir=games_dir)
            # The mover is whoever was *to move* before play_move pushed
            # the SAN — capture it before the board flips.
            mover = "white" if state.board.turn else "black"
            move_result = state.play_move(
                arguments["move_san"],
                by=arguments.get("by"),
                expected_ply=arguments.get("expected_ply"),
            )
            clock = load_clock(arguments["game_id"], games_dir=games_dir)
            if clock is not None and clock.started:
                clock.on_move_played(mover=mover)
                if clock.flag_fall is not None:
                    state.metadata.result = result_from_flag_fall(clock.flag_fall)
                    state.save()
                save_clock(clock, games_dir=games_dir)
            return _mcp_response(
                {
                    "status": "ok",
                    "move": move_result,
                    "state": state.to_state_dict(),
                    "clock": clock.to_dict() if clock is not None else None,
                }
            )

        if name == "chess_resign":
            state = GameState.load(arguments["game_id"], games_dir=games_dir)
            result = state.resign(by=arguments["by"])
            return _mcp_response(
                {
                    "status": "ok",
                    "resign": result,
                    "state": state.to_state_dict(),
                }
            )

        if name == "chess_offer_draw":
            state = GameState.load(arguments["game_id"], games_dir=games_dir)
            result = state.offer_draw(by=arguments["by"])
            return _mcp_response(
                {
                    "status": "ok",
                    "offer": result,
                    "state": state.to_state_dict(),
                }
            )

        if name == "chess_agree_draw":
            state = GameState.load(arguments["game_id"], games_dir=games_dir)
            result = state.agree_draw(by=arguments["by"])
            return _mcp_response(
                {
                    "status": "ok",
                    "agree": result,
                    "state": state.to_state_dict(),
                }
            )

        if name == "chess_decline_draw":
            state = GameState.load(arguments["game_id"], games_dir=games_dir)
            result = state.decline_draw(by=arguments["by"])
            return _mcp_response(
                {
                    "status": "ok",
                    "decline": result,
                    "state": state.to_state_dict(),
                }
            )

        if name == "chess_get_raw_pgn":
            state = GameState.load(arguments["game_id"], games_dir=games_dir)
            return _mcp_response({"status": "ok", "pgn": state.raw_pgn()})

        if name == "chess_post_commentary":
            entry = post_commentary(
                arguments["game_id"],
                ply=arguments["ply"],
                source=arguments["source"],
                text=arguments["text"],
                games_dir=games_dir,
            )
            return _mcp_response({"status": "ok", "entry": entry})

        if name == "chess_get_commentary":
            entries = get_commentary(
                arguments["game_id"],
                ply=arguments.get("ply"),
                games_dir=games_dir,
            )
            return _mcp_response({"status": "ok", "count": len(entries), "entries": entries})

        if name == "chess_clock_state":
            clock = load_clock(arguments["game_id"], games_dir=games_dir)
            return _mcp_response(
                {
                    "status": "ok",
                    "clock": clock.to_dict() if clock is not None else None,
                }
            )

        if name == "chess_clock_start":
            clock = load_clock(arguments["game_id"], games_dir=games_dir)
            if clock is None:
                return _mcp_response(
                    {
                        "status": "error",
                        "code": "NO_CLOCK",
                        "reason": "Game has no clock (TimeControl=unrated).",
                    }
                )
            clock.start()
            save_clock(clock, games_dir=games_dir)
            return _mcp_response({"status": "ok", "clock": clock.to_dict()})

        if name == "chess_board_facts":
            fen = arguments.get("fen")
            game_id = arguments.get("game_id")
            if fen and game_id:
                return _mcp_response(
                    {
                        "status": "error",
                        "code": "INVALID_ARG",
                        "reason": "chess_board_facts : ``game_id`` et ``fen`` sont exclusifs, fournir un seul.",
                    }
                )
            chess_board: chess.Board
            if fen:
                try:
                    chess_board = chess.Board(fen)
                except ValueError as exc:
                    return _mcp_response(
                        {
                            "status": "error",
                            "code": "INVALID_ARG",
                            "reason": f"chess_board_facts : FEN invalide ({exc}).",
                        }
                    )
            elif game_id:
                state = GameState.load(game_id, games_dir=games_dir)
                chess_board = state.board
            else:
                return _mcp_response(
                    {
                        "status": "error",
                        "code": "INVALID_ARG",
                        "reason": "chess_board_facts : fournir ``game_id`` ou ``fen``.",
                    }
                )
            facts = _compute_board_facts(chess_board)
            facts_payload: dict[str, Any] = {"status": "ok", "facts": facts}
            expected = arguments.get("expected")
            if expected:
                facts_payload["expected"] = expected
                if game_id:
                    _journal_board_facts_gap(game_id, chess_board, expected, facts)
            move_san = arguments.get("move_san")
            if move_san:
                # move_san can be str or list ; _compute_move_facts handles both
                # and returns {sequence, stopped_at?, error?, material_final?}.
                # Illegal moves are captured INSIDE the sequence result — no
                # exception bubbles up. Caller inspects ``stopped_at`` to know
                # if the sequence was truncated.
                facts_payload["move_facts"] = _compute_move_facts(chess_board, move_san)
            return _mcp_response(facts_payload)

        if name == "chess_analyze_position":
            depth = int(arguments.get("depth") or DEFAULT_DEPTH)
            fen = arguments.get("fen")
            game_id = arguments.get("game_id")
            if not fen and not game_id:
                return _mcp_response(
                    {
                        "status": "error",
                        "code": "MISSING_INPUT",
                        "reason": "chess_analyze_position needs either fen or game_id.",
                    }
                )
            ply_arg = arguments.get("ply")
            decision = check_engine_call(
                games_dir,
                tool=name,
                game_id=game_id,
                fen=fen,
                ply=int(ply_arg) if ply_arg is not None else None,
            )
            log_engine_call(
                tool=name,
                decision=decision,
                game_id=game_id,
                fen=fen,
                caller=arguments.get("caller"),
            )
            if not decision["allowed"]:
                return _mcp_response(
                    {
                        "status": "error",
                        "code": "ENGINE_FORBIDDEN_INPROGRESS",
                        "reason": decision["reason"],
                        "matched_game_id": decision["matched_game_id"],
                    }
                )
            if not fen:
                assert game_id is not None  # narrows for mypy — guarded by earlier check
                state = GameState.load(game_id, games_dir=games_dir)
                if ply_arg is None:
                    fen = state.board.fen()
                else:
                    import chess as _chess

                    replay = _chess.Board()
                    for san in state.san_history[: int(ply_arg)]:
                        replay.push(replay.parse_san(san))
                    fen = replay.fen()
            analysis = analyze_position(fen, depth=depth)
            return _mcp_response({"status": "ok", "analysis": analysis.to_dict()})

        if name == "chess_analyze_game":
            depth = int(arguments.get("depth") or DEFAULT_DEPTH)
            game_id = arguments["game_id"]
            decision = check_engine_call(games_dir, tool=name, game_id=game_id)
            if not decision["allowed"]:
                log_engine_call(
                    tool=name,
                    decision=decision,
                    game_id=game_id,
                    caller=arguments.get("caller"),
                )
                return _mcp_response(
                    {
                        "status": "error",
                        "code": "ENGINE_FORBIDDEN_INPROGRESS",
                        "reason": decision["reason"],
                        "matched_game_id": decision["matched_game_id"],
                    }
                )
            state = GameState.load(game_id, games_dir=games_dir)
            walk = analyze_game(state, depth=depth)
            walk, mask_count, mask_details = mask_walk_entries(walk, games_dir)
            log_engine_call(
                tool=name,
                decision=decision,
                game_id=game_id,
                caller=arguments.get("caller"),
                extra={"masked_plies": mask_details} if mask_details else None,
            )
            return _mcp_response(
                {
                    "status": "ok",
                    "count": len(walk),
                    "walk": [e.to_dict() for e in walk],
                    "masked_count": mask_count,
                    "masked_plies": mask_details,
                }
            )

        if name == "chess_get_my_mistakes":
            depth = int(arguments.get("depth") or DEFAULT_DEPTH)
            min_classification = arguments.get("min_classification", "inaccuracy")
            game_id = arguments["game_id"]
            decision = check_engine_call(games_dir, tool=name, game_id=game_id)
            if not decision["allowed"]:
                log_engine_call(
                    tool=name,
                    decision=decision,
                    game_id=game_id,
                    caller=arguments.get("caller"),
                )
                return _mcp_response(
                    {
                        "status": "error",
                        "code": "ENGINE_FORBIDDEN_INPROGRESS",
                        "reason": decision["reason"],
                        "matched_game_id": decision["matched_game_id"],
                    }
                )
            state = GameState.load(game_id, games_dir=games_dir)
            walk = analyze_game(state, depth=depth)
            walk, mask_count, mask_details = mask_walk_entries(walk, games_dir)
            log_engine_call(
                tool=name,
                decision=decision,
                game_id=game_id,
                caller=arguments.get("caller"),
                extra={"masked_plies": mask_details} if mask_details else None,
            )
            mistakes = get_my_mistakes(
                walk,
                mover=arguments["player"],
                min_classification=min_classification,
            )
            return _mcp_response(
                {
                    "status": "ok",
                    "count": len(mistakes),
                    "mistakes": [e.to_dict() for e in mistakes],
                    "masked_count": mask_count,
                    "masked_plies": mask_details,
                }
            )

        return _mcp_response({"status": "error", "code": "UNKNOWN_TOOL", "name": name})

    except StalePlyError as exc:
        return _mcp_response(
            {
                "status": "error",
                "code": "STALE_PLY",
                "current_ply": exc.current_ply,
                "expected_ply": exc.expected_ply,
            }
        )
    except IllegalMoveError as exc:
        return _mcp_response({"status": "error", "code": "ILLEGAL_MOVE", "reason": str(exc)})
    except GameNotFoundError as exc:
        return _mcp_response({"status": "error", "code": "GAME_NOT_FOUND", "reason": str(exc)})
    except GameAlreadyExistsError as exc:
        return _mcp_response({"status": "error", "code": "GAME_ALREADY_EXISTS", "reason": str(exc)})
    except InvalidGameStateError as exc:
        return _mcp_response({"status": "error", "code": "INVALID_STATE", "reason": str(exc)})
    except ChessboardError as exc:
        return _mcp_response({"status": "error", "code": "CHESSBOARD_ERROR", "reason": str(exc)})
    except EngineUnavailableError as exc:
        return _mcp_response({"status": "error", "code": "ENGINE_UNAVAILABLE", "reason": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return _mcp_response({"status": "error", "code": "INTERNAL", "reason": str(exc)})


async def _run_stdio() -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Entry point for ``python -m palamede_chessboard.mcp_chessboard_server``."""
    asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
