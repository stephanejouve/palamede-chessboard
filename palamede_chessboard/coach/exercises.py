"""Exercises drawn from the mistakes of a report (#95).

The position just before a mistake becomes an exercise, in one of two
kinds :

* ``find_move`` — « find the right move », one move in step 1. Checked
  with the engine on the free position, two lines (MultiPV 2), and kept
  only when its solution is unique or clearly better (:func:`clear_solution`).
* ``attacked`` — « what is attacked ? », when one of the mover's pieces
  is already en prise in that position : the answer is those squares,
  read on the board, without the engine.

Only the motifs read on facts give exercises : ``missed_mate``,
``hanging_piece`` and ``ignored_threat``. The heuristic ones, and
``other``, stay in the report only (Leader, #95).

Each engine call goes through the guard as ``chess_analyze_position``
on a FEN, and leaves its line in the engine log : a position a live game
reached is refused, and the whole game waits for a later round. The
solution is kept in ``.coach/players/`` only, never in a game.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import chess
import chess.engine

from palamede_chessboard.chess_engine_guard import check_engine_call, log_engine_call
from palamede_chessboard.chessboard_engine import (
    DEFAULT_DEPTH,
    DEFAULT_TIMEOUT_SECONDS,
    MISTAKE_THRESHOLD_CP,
    _score_to_normalized_cp,
    get_engine_path,
)
from palamede_chessboard.coach import CALLER
from palamede_chessboard.coach.motifs import _hanging

#: The motifs an exercise is drawn from : facts, never one of ``HEURISTIC_MOTIFS``.
EXERCISE_MOTIFS = frozenset({"missed_mate", "hanging_piece", "ignored_threat"})
#: The motifs that also ask « what is attacked ? ».
ATTACKED_MOTIFS = frozenset({"hanging_piece", "ignored_threat"})
#: How far ahead of the second line the best must be, without a mate.
GAP_CP = 200
#: Below this, for the side to move, the best line is losing : no exercise.
LOSING_CP = -MISTAKE_THRESHOLD_CP
ENGINE_TOOL = "chess_analyze_position"


@dataclass(frozen=True)
class Line:
    """One line of the engine, scored for the side to move."""

    uci: str
    cp: int  # a mate is ±MATE_SCORE_CP, shorter mates higher
    mates: bool  # the side to move mates


def best_two(
    fen: str,
    *,
    depth: int = DEFAULT_DEPTH,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    engine_path: str | None = None,
) -> list[Line]:
    """The two best lines of ``fen`` (MultiPV 2), best first ; fewer if fewer moves."""
    board = chess.Board(fen)
    path = engine_path or get_engine_path()
    limit = chess.engine.Limit(depth=depth, time=timeout)
    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        infos = engine.analyse(board, limit, multipv=2)
    lines = []
    for info in infos:
        pv, score = info.get("pv"), info.get("score")
        if not pv or score is None:
            continue
        cp = _score_to_normalized_cp(score)
        if cp is None:
            continue
        mate = score.pov(board.turn).mate()
        lines.append(
            Line(
                uci=pv[0].uci(),
                cp=cp if board.turn == chess.WHITE else -cp,
                mates=mate is not None and mate > 0,
            )
        )
    return sorted(lines, key=lambda line: line.cp, reverse=True)


def clear_solution(lines: list[Line], played_uci: str) -> tuple[Line, int] | None:
    """The solution and its lead over the second line, or ``None``.

    Kept when the best line mates and the second does not, or mates
    later ; or when the best is :data:`GAP_CP` ahead and not losing. A
    position with one legal move has nothing to find, and a best move
    that is the one played was no mistake to learn from.
    """
    if len(lines) < 2:
        return None
    best, second = lines[0], lines[1]
    if best.uci == played_uci:
        return None
    gap = best.cp - second.cp
    if best.mates:
        return (best, gap) if gap > 0 else None
    if gap >= GAP_CP and best.cp > LOSING_CP:
        return best, gap
    return None


def exercise_id(kind: str, fen: str) -> str:
    """Same kind and position, same exercise : the move counters aside."""
    position = " ".join(fen.split()[:4])
    return hashlib.sha256(f"{kind}|{position}".encode()).hexdigest()[:16]


def _exercise(report: dict, side: str, mistake: dict, kind: str, **solution: object) -> dict:
    fen = mistake["fen_before"]
    return {
        "id": exercise_id(kind, fen),
        "kind": kind,
        "source_game_id": report["game_id"],
        "ply": mistake["ply"],
        "fen": fen,
        "side": side,
        "motif": mistake["motif"],
        **solution,
        "state": "proposed",
        "attempts": [],
        "game_ids": [],
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def exercises_of(
    report: dict,
    games_dir: Path,
    *,
    depth: int = DEFAULT_DEPTH,
    engine_path: str | None = None,
) -> dict[str, list[dict]] | None:
    """``{player_id: exercises}`` drawn from ``report``, or ``None`` to wait a round."""
    found: dict[str, list[dict]] = {}
    for side, data in report["sides"].items():
        exercises = found.setdefault(data["player"], [])
        for mistake in data["mistakes"]:
            if mistake["motif"] not in EXERCISE_MOTIFS:
                continue
            fen = mistake["fen_before"]
            board = chess.Board(fen)
            attacked = sorted(_hanging(board, side))
            if mistake["motif"] in ATTACKED_MOTIFS and attacked:
                exercises.append(
                    _exercise(report, side, mistake, "attacked", solution_squares=attacked)
                )
            decision = check_engine_call(games_dir, tool=ENGINE_TOOL, fen=fen)
            extra = {"source_game_id": report["game_id"]}
            log_engine_call(
                tool=ENGINE_TOOL, decision=decision, fen=fen, caller=CALLER, extra=extra
            )
            if not decision["allowed"]:
                return None  # a live game reached this position : later
            lines = best_two(fen, depth=depth, engine_path=engine_path)
            kept = clear_solution(lines, board.parse_san(mistake["san"]).uci())
            if kept is None:
                continue
            line, gap = kept
            exercises.append(
                _exercise(
                    report,
                    side,
                    mistake,
                    "find_move",
                    solution_uci=line.uci,
                    solution_san=board.san(chess.Move.from_uci(line.uci)),
                    gap_cp=gap,
                )
            )
    return found
