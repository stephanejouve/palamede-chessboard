"""Exercises drawn from the coach's reports (#95).

Synthetic games and players named by function. The fake engine scores
each position from a table for the game walk, and gives two prepared
lines for MultiPV, as Stockfish does.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import chess
import chess.engine
import pytest

from palamede_chessboard.chessboard_engine import MATE_SCORE_CP
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.coach import exercises
from palamede_chessboard.coach.diagnosis import run_once
from palamede_chessboard.coach.exercises import (
    GAP_CP,
    Line,
    best_two,
    clear_solution,
    exercise_id,
    exercises_of,
)
from palamede_chessboard.coach.motifs import HEURISTIC_MOTIFS
from palamede_chessboard.coach.store import (
    CoachStoreError,
    add_exercises,
    player_path,
    read_exercises,
    read_report,
)
from palamede_chessboard.players import registry

HUMAN, AGENT = "human-player", "agent-player"
#: 1.e4 Nf6 2.e5 h6?? : the knight attacked by the pawn stays en prise.
THREAT = ["e4", "Nf6", "e5", "h6"]


def _fen_after(moves: list[str]) -> str:
    board = chess.Board()
    for san in moves:
        board.push_san(san)
    return board.fen()


BEFORE_H6 = _fen_after(THREAT[:3])
WHITE = chess.WHITE


def _pov(cp: int | None = None, mate: int | None = None) -> chess.engine.PovScore:
    score = chess.engine.Mate(mate) if mate is not None else chess.engine.Cp(cp or 0)
    return chess.engine.PovScore(score, WHITE)


class FakeEngine:
    """The walk from ``table`` (FEN → white-POV score), MultiPV from ``lines``."""

    def __init__(self, table: dict, lines: dict) -> None:
        self.table, self.lines = table, lines
        self.multipv_fens: list[str] = []

    def analyse(self, board: chess.Board, limit: object, multipv: int | None = None):
        if multipv is not None:
            self.multipv_fens.append(board.fen())
            return [
                {"pv": [chess.Move.from_uci(uci)], "score": score}
                for uci, score in self.lines.get(board.fen(), [])
            ]
        if board.is_checkmate():
            return {"score": chess.engine.PovScore(chess.engine.Mate(0), board.turn)}
        return {"score": self.table.get(board.fen()) or _pov(0)}


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine(
        # 2...h6?? costs black 300 : a blunder.
        table={_fen_after(THREAT): _pov(300)},
        # Black to move : Nd5 holds, Ng4 is 250 worse (white POV).
        lines={BEFORE_H6: [("f6d5", _pov(0)), ("f6g4", _pov(250))]},
    )


@pytest.fixture
def popen(engine: FakeEngine):
    cm = MagicMock()
    cm.__enter__.return_value = engine
    target = "chess.engine.SimpleEngine.popen_uci"
    with patch(target, return_value=cm) as mock:
        yield mock


@pytest.fixture
def gdir(tmp_path: Path) -> Path:
    registry.add_player(tmp_path, HUMAN, kind="human")
    registry.add_player(tmp_path, AGENT, kind="agent")
    return tmp_path


def _threat_game(gdir: Path, game_id: str = "g1") -> GameState:
    meta = GameMetadata(
        game_id=game_id, white=AGENT, black=HUMAN, time_control=TimeControl(format="unrated")
    )
    state = GameState.create(meta, games_dir=gdir)
    for san in THREAT:
        state.play_move(san)
    state.resign(by="black")
    return state


def _log(engine_log: Path) -> list[dict]:
    return [json.loads(line) for line in engine_log.read_text(encoding="utf-8").splitlines()]


# --- the selection rule -------------------------------------------------------


def _line(uci: str, cp: int, mates: bool = False) -> Line:
    return Line(uci=uci, cp=cp, mates=mates)


@pytest.mark.parametrize(
    ("lines", "kept"),
    [
        # A mate against no mate, a mate against a slower one.
        ([_line("a", MATE_SCORE_CP - 1, True), _line("b", 50)], True),
        ([_line("a", MATE_SCORE_CP - 1, True), _line("b", MATE_SCORE_CP - 3, True)], True),
        # Two mates as fast : no single solution.
        ([_line("a", MATE_SCORE_CP - 2, True), _line("b", MATE_SCORE_CP - 2, True)], False),
        ([_line("a", 150), _line("b", 150 - GAP_CP)], True),
        ([_line("a", 150), _line("b", 150 - GAP_CP + 1)], False),
        # Clearly better, but still losing for the side to move.
        ([_line("a", -100), _line("b", -100 - GAP_CP)], False),
        ([_line("a", -99), _line("b", -99 - GAP_CP)], True),
        # One legal move : nothing to find.
        ([_line("a", 500)], False),
        ([], False),
    ],
)
def test_clear_solution(lines: list[Line], kept: bool) -> None:
    result = clear_solution(lines, played_uci="z")
    assert (result is not None) == kept
    if kept:
        assert result == (lines[0], lines[0].cp - lines[1].cp)


def test_the_move_played_is_no_solution() -> None:
    assert clear_solution([_line("a", 400), _line("b", 0)], played_uci="a") is None


def test_best_two_scores_for_the_side_to_move(engine: FakeEngine, popen) -> None:
    lines = best_two(BEFORE_H6, engine_path="/fake")  # black to move
    assert lines == [_line("f6d5", 0), _line("f6g4", -250)]
    popen.assert_called_once_with("/fake")


def test_best_two_reads_a_mate_and_sorts(engine: FakeEngine, popen) -> None:
    engine.lines[chess.STARTING_FEN] = [("e2e4", _pov(30)), ("d2d4", _pov(mate=2))]
    lines = best_two(chess.STARTING_FEN, engine_path="/fake")
    assert [(line.uci, line.mates) for line in lines] == [("d2d4", True), ("e2e4", False)]
    assert lines[0].cp == MATE_SCORE_CP - 2
    # Black to move and mated : a mate, but not one black gives.
    fen = BEFORE_H6
    engine.lines[fen] = [("f6d5", _pov(mate=3)), ("f6g4", _pov(mate=1))]
    assert [line.mates for line in best_two(fen, engine_path="/fake")] == [False, False]


def test_the_id_is_the_position_not_the_move_counters() -> None:
    other_counters = BEFORE_H6.rsplit(" ", 2)[0] + " 7 30"
    assert exercise_id("find_move", BEFORE_H6) == exercise_id("find_move", other_counters)
    assert exercise_id("find_move", BEFORE_H6) != exercise_id("attacked", BEFORE_H6)


# --- from a report -------------------------------------------------------------


def _report(motif: str, heuristic: bool = False, san: str = "h6", fen: str = BEFORE_H6) -> dict:
    mistake = {
        "ply": 4,
        "san": san,
        "fen_before": fen,
        "classification": "blunder",
        "cp_loss": 300,
        "missed_mate": None,
        "motif": motif,
        "heuristic": heuristic,
    }
    return {"game_id": "g1", "sides": {"black": {"player": HUMAN, "mistakes": [mistake]}}}


def test_an_ignored_threat_gives_both_kinds(gdir: Path, popen, engine_log: Path) -> None:
    found = exercises_of(_report("ignored_threat"), gdir, engine_path="/fake")
    attacked, find_move = found[HUMAN]
    assert attacked["kind"] == "attacked" and attacked["solution_squares"] == ["f6"]
    assert find_move["kind"] == "find_move"
    assert (find_move["solution_uci"], find_move["solution_san"]) == ("f6d5", "Nd5")
    assert find_move["gap_cp"] == 250
    for exercise in (attacked, find_move):
        assert exercise["state"] == "proposed" and exercise["attempts"] == []
        assert (exercise["source_game_id"], exercise["ply"]) == ("g1", 4)
        assert (exercise["fen"], exercise["side"]) == (BEFORE_H6, "black")
    [line] = _log(engine_log)
    assert (line["tool"], line["fen"], line["caller"]) == (
        "chess_analyze_position",
        BEFORE_H6,
        "coach",
    )
    assert (line["decision"], line["source_game_id"]) == ("allowed", "g1")


@pytest.mark.parametrize(("motif", "heuristic"), [("other", False), ("weakened_king", True)])
def test_no_exercise_from_heuristics_or_other(gdir: Path, popen, motif, heuristic) -> None:
    assert exercises_of(_report(motif, heuristic), gdir, engine_path="/fake") == {HUMAN: []}
    popen.assert_not_called()


def test_heuristics_never_give_an_exercise() -> None:
    assert not exercises.EXERCISE_MOTIFS & HEURISTIC_MOTIFS
    assert exercises.ATTACKED_MOTIFS <= exercises.EXERCISE_MOTIFS


def test_nothing_attacked_yet_asks_no_question(gdir: Path, popen) -> None:
    # 1.e4, black to move : nothing of black is en prise before the mistake.
    report = _report("hanging_piece", san="e5", fen=_fen_after(["e4"]))
    assert exercises_of(report, gdir, engine_path="/fake") == {HUMAN: []}


def test_a_missed_mate_asks_no_question_on_attacks(gdir: Path, popen) -> None:
    [exercise] = exercises_of(_report("missed_mate"), gdir, engine_path="/fake")[HUMAN]
    assert exercise["kind"] == "find_move"


def test_no_find_move_without_a_clear_solution(gdir: Path, popen, engine) -> None:
    engine.lines[BEFORE_H6] = [("f6d5", _pov(0)), ("f6g4", _pov(100))]
    [exercise] = exercises_of(_report("ignored_threat"), gdir, engine_path="/fake")[HUMAN]
    assert exercise["kind"] == "attacked"


def test_a_position_refused_by_the_guard_waits(
    gdir: Path, popen, engine_log: Path, monkeypatch
) -> None:
    refused = {"allowed": False, "matched_game_id": "live", "reason": "shared position"}
    seen = []

    def guard(games_dir, **kwargs):
        seen.append((games_dir, kwargs))
        return refused

    monkeypatch.setattr(exercises, "check_engine_call", guard)
    assert exercises_of(_report("ignored_threat"), gdir, engine_path="/fake") is None
    popen.assert_not_called()
    assert seen == [(gdir, {"tool": "chess_analyze_position", "fen": BEFORE_H6})]
    [line] = _log(engine_log)
    assert (line["decision"], line["matched_game_id"], line["caller"]) == (
        "refused",
        "live",
        "coach",
    )


# --- the store of a player -------------------------------------------------------


def test_adding_twice_keeps_the_state(gdir: Path) -> None:
    first = {"id": "e1", "state": "proposed"}
    assert add_exercises(gdir, HUMAN, [first, {"id": "e1", "state": "proposed"}]) == 1
    stored = read_exercises(gdir, HUMAN)
    stored[0]["state"] = "solved"
    path = player_path(gdir, HUMAN)
    path.write_text(json.dumps({"exercises": stored}), encoding="utf-8")
    assert add_exercises(gdir, HUMAN, [{"id": "e1", "state": "proposed"}, {"id": "e2"}]) == 1
    assert [(e["id"], e.get("state")) for e in read_exercises(gdir, HUMAN)] == [
        ("e1", "solved"),
        ("e2", None),
    ]


def test_exercises_are_private(gdir: Path) -> None:
    (gdir / ".coach").mkdir(mode=0o755)
    add_exercises(gdir, HUMAN, [{"id": "e1"}])
    path = player_path(gdir, HUMAN)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("content", ["{", '{"exercises": {}}', "[]"])
def test_an_unreadable_store_is_never_overwritten(gdir: Path, content: str) -> None:
    add_exercises(gdir, HUMAN, [{"id": "e1"}])
    path = player_path(gdir, HUMAN)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(CoachStoreError):
        add_exercises(gdir, HUMAN, [{"id": "e2"}])
    with pytest.raises(CoachStoreError):
        read_exercises(gdir, HUMAN)
    assert path.read_text(encoding="utf-8") == content


def test_no_exercises_yet(gdir: Path) -> None:
    assert read_exercises(gdir, HUMAN) == []


@pytest.mark.parametrize("player_id", ["../escape", "a/b", ""])
def test_a_player_path_never_leaves_its_directory(gdir: Path, player_id: str) -> None:
    with pytest.raises(registry.RegistryError):
        player_path(gdir, player_id)


# --- one round ---------------------------------------------------------------------


def test_a_round_draws_the_exercises_once(gdir: Path, popen, engine: FakeEngine) -> None:
    _threat_game(gdir)
    summary = run_once(gdir, engine_path="/fake")
    assert summary["diagnosed"] == ["g1"] and summary["exercises"] == 2
    kinds = [e["kind"] for e in read_exercises(gdir, HUMAN)]
    assert kinds == ["attacked", "find_move"]
    assert engine.multipv_fens == [BEFORE_H6]
    assert run_once(gdir, engine_path="/fake")["exercises"] == 0
    assert not player_path(gdir, AGENT).exists()


def test_the_solution_is_in_no_game(gdir: Path, popen) -> None:
    _threat_game(gdir)
    run_once(gdir, engine_path="/fake")
    for path in gdir.iterdir():
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "Nd5" not in text and "f6d5" not in text, path.name
    assert "f6d5" not in json.dumps(read_report(gdir, "g1"))


def test_a_refused_position_writes_neither_report_nor_exercises(
    gdir: Path, popen, monkeypatch
) -> None:
    _threat_game(gdir)
    refused = {"allowed": False, "matched_game_id": "live", "reason": "shared position"}
    guard = exercises.check_engine_call
    monkeypatch.setattr(exercises, "check_engine_call", lambda *a, **k: refused)
    assert run_once(gdir, engine_path="/fake")["waiting"] == ["g1"]
    assert read_report(gdir, "g1") is None
    assert read_exercises(gdir, HUMAN) == []
    # Not monkeypatch.undo() : it would also undo the engine log of the test.
    monkeypatch.setattr(exercises, "check_engine_call", guard)
    assert run_once(gdir, engine_path="/fake")["exercises"] == 2


def test_an_unreadable_store_fails_the_game_and_keeps_it(gdir: Path, popen) -> None:
    _threat_game(gdir)
    path = player_path(gdir, HUMAN)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    assert run_once(gdir, engine_path="/fake")["failed"] == ["g1"]
    assert read_report(gdir, "g1") is None  # retried on the next round
    assert path.read_text(encoding="utf-8") == "{"
