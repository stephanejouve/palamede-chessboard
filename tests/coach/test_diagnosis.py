"""The coach's diagnosis and its round over the games (#95).

Synthetic games and players named by function ; the engine is a fake
that scores each position from a table, so the same position always
gets the same score, as the reuse of the walk assumes.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import chess
import chess.engine
import pytest

from palamede_chessboard.chessboard_engine import GameWalkEntry, analyze_game, get_my_mistakes
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.coach import diagnosis
from palamede_chessboard.coach.diagnosis import diagnose, human_sides, run_once
from palamede_chessboard.coach.store import (
    game_key,
    read_report,
    report_path,
    write_report,
)
from palamede_chessboard.players import registry

HUMAN, AGENT = "human-player", "agent-player"
#: 1.e4 e5 2.Qh5 Nc6 3.Bc4 Nf6?? 4.Qxf7#
SCHOLAR = ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"]


class FakeEngine:
    """Scores a position from ``table`` (FEN → white-POV score), else 0."""

    def __init__(self, table: dict[str, chess.engine.PovScore]) -> None:
        self.table = table
        self.calls = 0

    def analyse(self, board: chess.Board, limit: object) -> dict:
        self.calls += 1
        if board.is_checkmate():
            return {"score": chess.engine.PovScore(chess.engine.Mate(0), board.turn)}
        score = self.table.get(board.fen())
        return {"score": score or chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE)}


def _scholar_table() -> dict[str, chess.engine.PovScore]:
    board = chess.Board()
    for san in SCHOLAR[:6]:
        board.push_san(san)
    after_nf6 = board.fen()
    board = chess.Board()
    for san in SCHOLAR[:4]:
        board.push_san(san)
    return {
        # 2...Nc6 costs black 60 : an inaccuracy, below what the coach reports.
        board.fen(): chess.engine.PovScore(chess.engine.Cp(60), chess.WHITE),
        # After 3...Nf6?? white mates in one.
        after_nf6: chess.engine.PovScore(chess.engine.Mate(1), chess.WHITE),
    }


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine(_scholar_table())


@pytest.fixture
def popen(engine: FakeEngine):
    cm = MagicMock()
    cm.__enter__.return_value = engine
    target = "palamede_chessboard.chessboard_engine.chess.engine.SimpleEngine.popen_uci"
    with patch(target, return_value=cm) as mock:
        yield mock


@pytest.fixture
def gdir(tmp_path: Path) -> Path:
    registry.add_player(tmp_path, HUMAN, kind="human")
    registry.add_player(tmp_path, AGENT, kind="agent")
    return tmp_path


def _game(gdir: Path, game_id: str, moves: list[str], white: str = AGENT, black: str = HUMAN):
    meta = GameMetadata(
        game_id=game_id, white=white, black=black, time_control=TimeControl(format="unrated")
    )
    state = GameState.create(meta, games_dir=gdir)
    for san in moves:
        state.play_move(san)
    return state


def test_the_walk_reused_is_the_walk(gdir: Path, engine: FakeEngine, popen) -> None:
    state = _game(gdir, "g1", SCHOLAR)
    twice = analyze_game(state, engine_path="/fake")
    calls_twice = engine.calls
    once = analyze_game(state, engine_path="/fake", reuse_after=True)
    assert [e.to_dict() for e in once] == [e.to_dict() for e in twice]
    assert calls_twice == 2 * len(SCHOLAR)
    assert engine.calls - calls_twice == len(SCHOLAR) + 1


def test_the_report_holds_get_my_mistakes(gdir: Path, popen) -> None:
    state = _game(gdir, "g1", SCHOLAR)
    report = diagnose(state, {"black": HUMAN}, gdir, engine_path="/fake")
    expected = get_my_mistakes(
        analyze_game(state, engine_path="/fake"), mover="black", min_classification="mistake"
    )
    mistakes = report["sides"]["black"]["mistakes"]
    assert [(m["ply"], m["san"]) for m in mistakes] == [(e.ply, e.san) for e in expected]
    assert [(m["ply"], m["san"], m["classification"]) for m in mistakes] == [(6, "Nf6", "blunder")]
    assert any(
        e.classification == "inaccuracy" and e.mover == "black"
        for e in analyze_game(state, engine_path="/fake")
    )
    assert report["sides"]["black"]["player"] == HUMAN
    assert sum(report["sides"]["black"]["by_motif"].values()) == 1
    assert "white" not in report["sides"]  # the agent's side is not diagnosed
    # The time axis of the progress : the game's Date, and the human's moves (#95 step 2).
    assert report["sides"]["black"]["plies"] == 3
    assert (report["game_date"], report["game_date_source"]) == (
        state.metadata.date.replace(".", "-"),
        "header",
    )


def test_a_mate_given_is_no_mistake_of_the_human(gdir: Path, popen) -> None:
    # The human plays white and mates : #197 counts no fault.
    state = _game(gdir, "g1", SCHOLAR, white=HUMAN, black=AGENT)
    report = diagnose(state, {"white": HUMAN}, gdir, engine_path="/fake")
    assert report["sides"]["white"]["mistakes"] == []


def test_a_game_in_progress_is_never_analysed(gdir: Path, popen) -> None:
    state = _game(gdir, "live", SCHOLAR[:4])
    assert run_once(gdir, engine_path="/fake") == {
        "diagnosed": [],
        "waiting": [],
        "failed": [],
        "deferred": [],
        "exercises": 0,
    }
    popen.assert_not_called()
    with pytest.raises(ValueError, match="in progress"):
        diagnose(state, {"black": HUMAN}, gdir, engine_path="/fake")
    popen.assert_not_called()
    assert read_report(gdir, "live") is None


def test_a_game_without_human_is_not_diagnosed(gdir: Path, popen) -> None:
    _game(gdir, "bots", SCHOLAR, white=AGENT, black="grandmaster name")
    assert run_once(gdir, engine_path="/fake")["diagnosed"] == []
    popen.assert_not_called()


def test_a_round_is_idempotent_even_when_the_pgn_is_rewritten(gdir: Path, popen) -> None:
    _game(gdir, "g1", SCHOLAR)
    assert run_once(gdir, engine_path="/fake")["diagnosed"] == ["g1"]
    first = report_path(gdir, "g1").read_text(encoding="utf-8")
    popen.reset_mock()
    assert run_once(gdir, engine_path="/fake")["diagnosed"] == []
    # A rewrite of the file (as #199-#201 do) leaves the game, and its key, unchanged.
    GameState.load("g1", games_dir=gdir).save()
    assert run_once(gdir, engine_path="/fake")["diagnosed"] == []
    popen.assert_not_called()
    assert report_path(gdir, "g1").read_text(encoding="utf-8") == first


def _as_written_before_the_progress(gdir: Path, game_id: str) -> None:
    """The report as the coach wrote it before game_date and plies (#95 step 2)."""
    report = read_report(gdir, game_id)
    assert report is not None
    del report["game_date"], report["game_date_source"]
    for found in report["sides"].values():
        del found["plies"]
    write_report(gdir, game_id, report)


def test_an_older_report_is_completed_without_the_engine(gdir: Path, popen) -> None:
    state = _game(gdir, "g1", SCHOLAR)
    run_once(gdir, engine_path="/fake")
    _as_written_before_the_progress(gdir, "g1")
    popen.reset_mock()
    assert run_once(gdir, engine_path="/fake")["diagnosed"] == []
    popen.assert_not_called()
    report = read_report(gdir, "g1")
    assert report is not None
    assert report["game_date"] == state.metadata.date.replace(".", "-")
    assert report["game_date_source"] == "header"
    assert report["sides"]["black"]["plies"] == 3
    # Completed once : the next round leaves the file as it is.
    completed = report_path(gdir, "g1").read_text(encoding="utf-8")
    run_once(gdir, engine_path="/fake")
    assert report_path(gdir, "g1").read_text(encoding="utf-8") == completed


def test_a_report_that_cannot_be_completed_stops_no_round(
    gdir: Path, popen, monkeypatch: pytest.MonkeyPatch
) -> None:
    _game(gdir, "g1", SCHOLAR)
    run_once(gdir, engine_path="/fake")
    _as_written_before_the_progress(gdir, "g1")
    _game(gdir, "g2", SCHOLAR)
    real = diagnosis.write_report

    def write(games_dir: Path, game_id: str, report: dict) -> Path:
        if game_id == "g1":
            raise OSError("read-only")
        return real(games_dir, game_id, report)

    monkeypatch.setattr(diagnosis, "write_report", write)
    # g1 stays as it was, g2 after it is still diagnosed (review Leader #217).
    assert run_once(gdir, engine_path="/fake")["diagnosed"] == ["g2"]
    report = read_report(gdir, "g1")
    assert report is not None and "game_date" not in report


def test_an_older_report_without_date_is_completed_undated(gdir: Path, popen) -> None:
    # No Date header : its diagnosis day is not the game's (Leader, #95).
    pgn = f'[White "{AGENT}"]\n[Black "{HUMAN}"]\n[Result "1-0"]\n\n1. e4 e5 2. Qh5 Nc6 1-0\n'
    (gdir / "undated.pgn").write_text(pgn, encoding="utf-8")
    run_once(gdir, engine_path="/fake")
    _as_written_before_the_progress(gdir, "undated")
    run_once(gdir, engine_path="/fake")
    report = read_report(gdir, "undated")
    assert report is not None
    assert (report["game_date"], report["game_date_source"]) == (None, None)
    assert report["sides"]["black"]["plies"] == 2


def test_an_older_file_rewritten_is_not_diagnosed_again(gdir: Path, popen) -> None:
    # Written before #199/#200 : minutes without marker, the note in Termination.
    pgn = (
        f'[White "{AGENT}"]\n[Black "{HUMAN}"]\n[Result "1-0"]\n[TimeControl "15+30"]\n'
        '[Termination "black resigns"]\n\n1. e4 e5 2. Qh5 Nc6 {black resigns} 1-0\n'
    )
    path = gdir / "old.pgn"
    path.write_text(pgn, encoding="utf-8")
    assert run_once(gdir, engine_path="/fake")["diagnosed"] == ["old"]
    GameState.load("old", games_dir=gdir).save()
    assert path.read_text(encoding="utf-8") != pgn  # the bytes changed
    popen.reset_mock()
    assert run_once(gdir, engine_path="/fake")["diagnosed"] == []
    popen.assert_not_called()


def test_the_key_is_the_game_not_its_bytes(gdir: Path) -> None:
    state = _game(gdir, "g1", SCHOLAR)
    key = game_key(state)
    state.save()
    assert game_key(GameState.load("g1", games_dir=gdir)) == key
    other = _game(gdir, "g2", SCHOLAR[:6])
    other.resign(by="black")
    assert game_key(other) != key


def test_a_game_sharing_a_live_position_waits(
    gdir: Path, popen, monkeypatch, engine_log: Path
) -> None:
    _game(gdir, "g1", SCHOLAR)
    details = [{"ply": 5, "matched_game_id": "live"}]
    monkeypatch.setattr(diagnosis, "mask_walk_entries", lambda walk, _gdir: (walk, 1, details))
    summary = run_once(gdir, engine_path="/fake")
    assert summary["waiting"] == ["g1"] and summary["diagnosed"] == []
    assert read_report(gdir, "g1") is None
    [line] = _log_lines(engine_log)
    assert line["masked_plies"] == details and line["caller"] == "coach"


def _log_lines(engine_log: Path) -> list[dict]:
    return [json.loads(line) for line in engine_log.read_text(encoding="utf-8").splitlines()]


def test_each_analysis_is_in_the_engine_log(gdir: Path, popen, engine_log: Path) -> None:
    _game(gdir, "g1", SCHOLAR)
    assert run_once(gdir, engine_path="/fake")["diagnosed"] == ["g1"]
    [line] = _log_lines(engine_log)
    assert (line["tool"], line["game_id"], line["caller"]) == (
        "chess_get_my_mistakes",
        "g1",
        "coach",
    )
    assert line["decision"] == "allowed" and "masked_plies" not in line
    run_once(gdir, engine_path="/fake")  # already reported : no engine, no line
    assert len(_log_lines(engine_log)) == 1


def test_a_refusal_of_the_guard_is_logged_and_waits(
    gdir: Path, popen, engine_log: Path, monkeypatch
) -> None:
    _game(gdir, "g1", SCHOLAR)
    refused = {"allowed": False, "matched_game_id": "live", "reason": "shared position"}
    seen = []

    def guard(games_dir, **kwargs):
        seen.append((games_dir, kwargs))
        return refused

    monkeypatch.setattr(diagnosis, "check_engine_call", guard)
    assert run_once(gdir, engine_path="/fake")["waiting"] == ["g1"]
    popen.assert_not_called()
    assert seen == [(gdir, {"tool": "chess_get_my_mistakes", "game_id": "g1"})]
    [line] = _log_lines(engine_log)
    assert (line["decision"], line["matched_game_id"], line["caller"]) == (
        "refused",
        "live",
        "coach",
    )
    assert read_report(gdir, "g1") is None


def test_an_unreadable_registry_stops_the_round(gdir: Path, popen) -> None:
    _game(gdir, "g1", SCHOLAR)
    registry.registry_path(gdir).write_text("{", encoding="utf-8")
    with pytest.raises(registry.RegistryError):
        run_once(gdir, engine_path="/fake")
    popen.assert_not_called()


def test_one_failing_game_does_not_stop_the_round(gdir: Path, popen, monkeypatch) -> None:
    _game(gdir, "a1", SCHOLAR)
    _game(gdir, "b2", SCHOLAR)
    real = diagnosis.diagnose

    def flaky(state, *args, **kwargs):
        if state.metadata.game_id == "a1":
            raise chess.engine.EngineTerminatedError("engine died")
        return real(state, *args, **kwargs)

    monkeypatch.setattr(diagnosis, "diagnose", flaky)
    summary = run_once(gdir, engine_path="/fake")
    assert summary["failed"] == ["a1"] and summary["diagnosed"] == ["b2"]


def test_human_sides_by_registry_kind(gdir: Path) -> None:
    state = _game(gdir, "g1", [], white=HUMAN, black=AGENT)
    players = registry.load(gdir)["players"]
    assert human_sides(state, players) == {"white": HUMAN}
    state.metadata.white = "Not An Id With Spaces"
    assert human_sides(state, players) == {}


def test_reports_are_private(gdir: Path) -> None:
    (gdir / ".coach").mkdir(mode=0o755)  # made too open by someone else
    path = write_report(gdir, "g1", {"key": "k"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
    assert json.loads(path.read_text(encoding="utf-8")) == {"key": "k"}


def test_a_heuristic_motif_is_marked() -> None:
    fen = "r1bqkbnr/pppppppp/2n5/8/8/5N2/PPPPPPPP/RNBQ1RK1 w kq - 0 4"
    after = chess.Board(fen)
    after.push_san("h3")
    entry = GameWalkEntry(1, "white", "h3", fen, after.fen(), 0, -150, 150, "mistake")
    assert diagnosis._mistake(entry)["motif"] == "weakened_king"
    assert diagnosis._mistake(entry)["heuristic"] is True
    entry.missed_mate = 1
    assert diagnosis._mistake(entry)["heuristic"] is False


def test_an_unreadable_report_is_diagnosed_again(gdir: Path, popen) -> None:
    _game(gdir, "g1", SCHOLAR)
    write_report(gdir, "g1", {"key": "k"})
    report_path(gdir, "g1").write_text("{", encoding="utf-8")
    assert read_report(gdir, "g1") is None
    assert run_once(gdir, engine_path="/fake")["diagnosed"] == ["g1"]


def test_a_report_path_never_leaves_its_directory(gdir: Path) -> None:
    with pytest.raises(Exception, match="game_id"):
        report_path(gdir, "../escape")


def test_the_cli_runs_one_round(gdir: Path, popen, capsys) -> None:
    _game(gdir, "g1", SCHOLAR)
    # No Stockfish needed : the CI has none.
    with patch("palamede_chessboard.chessboard_engine.get_engine_path", return_value="/fake"):
        assert diagnosis.main(["once", "--games-dir", str(gdir), "--depth", "8"]) == 0
    assert json.loads(capsys.readouterr().out)["diagnosed"] == ["g1"]
