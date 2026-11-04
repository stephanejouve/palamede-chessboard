"""``palamede-coach run`` (#95, PR 4) : before the engine, and between rounds.

* a game one of whose positions a live game reached waits **before** the
  engine starts, and the refusal is in the engine log ;
* an unchanged game is not read again (mtime filter), the registry
  changing forgets them all ;
* a game that waited or failed is deferred for :data:`RETRY_SECONDS` ;
* ``stop`` is read between two games, not only between two rounds ;
* the loop makes rounds until stopped, a failing round included.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import chess
import chess.engine
import pytest

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.coach import diagnosis
from palamede_chessboard.coach.diagnosis import run_forever, run_once
from palamede_chessboard.coach.rounds import RETRY_SECONDS, RoundMemory, Wait
from palamede_chessboard.coach.store import read_report
from palamede_chessboard.players import registry
from tests.coach.test_diagnosis import SCHOLAR, FakeEngine, _scholar_table

HUMAN, AGENT = "human-player", "agent-player"
NOW = 1_000_000.0


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine(_scholar_table())


@pytest.fixture
def popen(engine: FakeEngine):
    cm = MagicMock()
    cm.__enter__.return_value = engine
    with patch("chess.engine.SimpleEngine.popen_uci", return_value=cm) as mock:
        yield mock


@pytest.fixture
def gdir(tmp_path: Path) -> Path:
    registry.add_player(tmp_path, HUMAN, kind="human")
    registry.add_player(tmp_path, AGENT, kind="agent")
    return tmp_path


def _game(gdir: Path, game_id: str, moves: list[str], fen: str | None = None) -> GameState:
    meta = GameMetadata(
        game_id=game_id, white=AGENT, black=HUMAN, time_control=TimeControl(format="unrated")
    )
    state = GameState.create(meta, games_dir=gdir, starting_fen=fen)
    for san in moves:
        state.play_move(san)
    return state


def _fen(moves: list[str]) -> str:
    board = chess.Board()
    for san in moves:
        board.push_san(san)
    return board.fen()


@pytest.fixture
def loads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The games read by the coach's rounds."""
    seen: list[str] = []
    real = GameState.load.__func__

    def load(cls, game_id, **kwargs):
        seen.append(game_id)
        return real(cls, game_id, **kwargs)

    monkeypatch.setattr(diagnosis.GameState, "load", classmethod(load))
    return seen


# --- before the engine -------------------------------------------------------------


def test_a_position_in_play_waits_before_the_engine(gdir: Path, popen, engine_log: Path) -> None:
    _game(gdir, "g1", SCHOLAR)
    # A game set up on a position of g1, still played : all its positions count.
    _game(gdir, "live", [], fen=_fen(SCHOLAR[:3]))
    assert run_once(gdir, engine_path="/fake")["waiting"] == ["g1"]
    popen.assert_not_called()
    [line] = [json.loads(x) for x in engine_log.read_text(encoding="utf-8").splitlines()]
    assert (line["decision"], line["matched_game_id"], line["caller"]) == (
        "refused",
        "live",
        "coach",
    )
    assert line["game_id"] == "g1"


def test_a_live_game_on_the_first_position_holds_it(gdir: Path, popen) -> None:
    start = _fen(["d4", "d5"])
    _game(gdir, "g1", ["c4", "e6", "Nc3", "Nf6"], fen=start)
    resigned = GameState.load("g1", games_dir=gdir)
    resigned.resign(by="black")
    _game(gdir, "live", [], fen=start)  # only g1's first position is shared
    assert run_once(gdir, engine_path="/fake")["waiting"] == ["g1"]
    popen.assert_not_called()


def test_the_opening_of_a_normal_live_game_does_not_hold_it(gdir: Path, popen) -> None:
    _game(gdir, "g1", SCHOLAR)
    _game(gdir, "live", SCHOLAR[:3])  # same opening, from the initial position
    assert run_once(gdir, engine_path="/fake")["diagnosed"] == ["g1"]


# --- the mtime filter ----------------------------------------------------------------


def test_an_unchanged_game_is_not_read_again(gdir: Path, popen, loads: list[str]) -> None:
    _game(gdir, "g1", SCHOLAR)
    _game(gdir, "live", ["d4"])
    memory = RoundMemory()
    assert run_once(gdir, engine_path="/fake", memory=memory, now=NOW)["diagnosed"] == ["g1"]
    loads.clear()
    run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 30)
    assert loads == []
    # A move in the live game : read again, and only it.
    live = GameState.load("live", games_dir=gdir)
    live.play_move("d5")
    loads.clear()
    run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 60)
    assert loads == ["live"]


def test_a_rewritten_pgn_is_read_but_not_diagnosed_again(gdir: Path, popen, loads) -> None:
    _game(gdir, "g1", SCHOLAR)
    memory = RoundMemory()
    run_once(gdir, engine_path="/fake", memory=memory, now=NOW)
    GameState.load("g1", games_dir=gdir).save()  # as #199-#201 rewrite older files
    loads.clear()
    assert run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 30)["diagnosed"] == []
    assert loads == ["g1"]


def test_a_change_of_the_registry_reads_them_all(gdir: Path, popen, loads) -> None:
    _game(gdir, "g1", SCHOLAR)
    memory = RoundMemory()
    run_once(gdir, engine_path="/fake", memory=memory, now=NOW)
    loads.clear()
    registry.add_player(gdir, "new-human", kind="human")  # a human game may appear
    run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 30)
    assert loads == ["g1"]


def test_a_move_while_the_game_is_read_is_seen_next_round(
    gdir: Path, popen, loads, monkeypatch
) -> None:
    _game(gdir, "live", ["e4"])
    memory = RoundMemory()
    real = diagnosis._one_game

    def one_game(pgn, *args, **kwargs):
        outcome = real(pgn, *args, **kwargs)
        GameState.load("live", games_dir=gdir).play_move("e5")  # played meanwhile
        return outcome

    monkeypatch.setattr(diagnosis, "_one_game", one_game)
    run_once(gdir, engine_path="/fake", memory=memory, now=NOW)
    monkeypatch.setattr(diagnosis, "_one_game", real)
    loads.clear()
    run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 30)
    assert loads == ["live"]


# --- the retry delay ----------------------------------------------------------------


def test_a_failed_game_is_deferred_then_retried(gdir: Path, popen, monkeypatch) -> None:
    _game(gdir, "g1", SCHOLAR)
    memory = RoundMemory()
    real = diagnosis.diagnose

    def broken(*args, **kwargs):
        raise chess.engine.EngineTerminatedError("engine died")

    monkeypatch.setattr(diagnosis, "diagnose", broken)
    assert run_once(gdir, engine_path="/fake", memory=memory, now=NOW)["failed"] == ["g1"]
    monkeypatch.setattr(diagnosis, "diagnose", real)
    summary = run_once(gdir, engine_path="/fake", memory=memory, now=NOW + RETRY_SECONDS - 1)
    assert summary["deferred"] == ["g1"] and summary["diagnosed"] == []
    later = run_once(gdir, engine_path="/fake", memory=memory, now=NOW + RETRY_SECONDS)
    assert later["diagnosed"] == ["g1"] and later["deferred"] == []
    assert memory.not_before == {}
    assert read_report(gdir, "g1") is not None


# --- waiting on a live game -----------------------------------------------------------


def _refused(engine_log: Path) -> int:
    if not engine_log.exists():
        return 0
    lines = [json.loads(x) for x in engine_log.read_text(encoding="utf-8").splitlines()]
    return sum(line["decision"] == "refused" for line in lines)


def test_a_waiting_game_waits_for_its_live_game_not_for_a_delay(
    gdir: Path, popen, engine_log: Path
) -> None:
    _game(gdir, "g1", SCHOLAR)
    live = _game(gdir, "live", [], fen=_fen(SCHOLAR[:3]))
    memory = RoundMemory()
    assert run_once(gdir, engine_path="/fake", memory=memory, now=NOW)["waiting"] == ["g1"]
    assert _refused(engine_log) == 1
    # Long after the retry delay, the live game unchanged : not tried, nothing logged.
    for later in (NOW + RETRY_SECONDS, NOW + 100 * RETRY_SECONDS):
        summary = run_once(gdir, engine_path="/fake", memory=memory, now=later)
        assert summary["deferred"] == ["g1"]
    assert _refused(engine_log) == 1
    live.resign(by="white")  # the live game ends : tried at the next round
    summary = run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 30)
    assert summary["diagnosed"] == ["g1", "live"] and memory.blocked == {}


def test_a_move_in_the_live_game_tries_again(gdir: Path, popen, engine_log: Path) -> None:
    _game(gdir, "g1", SCHOLAR)
    live = _game(gdir, "live", [], fen=_fen(SCHOLAR[:3]))
    memory = RoundMemory()
    run_once(gdir, engine_path="/fake", memory=memory, now=NOW)
    live.play_move("Nc6")  # still in play, still on g1's positions
    assert run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 30)["waiting"] == ["g1"]
    assert _refused(engine_log) == 2
    assert run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 60)["deferred"] == ["g1"]


def test_its_own_pgn_changing_tries_again(gdir: Path, popen) -> None:
    _game(gdir, "g1", SCHOLAR)
    _game(gdir, "live", [], fen=_fen(SCHOLAR[:3]))
    memory = RoundMemory()
    run_once(gdir, engine_path="/fake", memory=memory, now=NOW)
    GameState.load("g1", games_dir=gdir).save()
    assert run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 30)["waiting"] == ["g1"]


def test_a_change_of_the_registry_forgets_the_waits(gdir: Path, popen) -> None:
    _game(gdir, "g1", SCHOLAR)
    _game(gdir, "live", [], fen=_fen(SCHOLAR[:3]))
    memory = RoundMemory()
    run_once(gdir, engine_path="/fake", memory=memory, now=NOW)
    registry.add_player(gdir, "new-human", kind="human")
    assert run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 30)["waiting"] == ["g1"]


def test_a_game_masked_after_the_engine_waits_for_its_live_game(
    gdir: Path, popen, monkeypatch
) -> None:
    # A live game got there while the engine ran : the masked plies name it.
    _game(gdir, "g1", SCHOLAR)
    _game(gdir, "live", ["d4"])
    monkeypatch.setattr(diagnosis, "game_in_play", lambda *a: None)
    monkeypatch.setattr(
        diagnosis,
        "mask_walk_entries",
        lambda walk, games_dir: (walk, 1, [{"ply": 3, "matched_game_id": "live"}]),
    )
    memory = RoundMemory()
    assert run_once(gdir, engine_path="/fake", memory=memory, now=NOW)["waiting"] == ["g1"]
    assert set(memory.blocked["g1"]) == {gdir / "g1.pgn", gdir / "live.pgn"}


def test_a_live_game_ending_while_the_game_is_read_is_seen(gdir: Path, popen, monkeypatch) -> None:
    _game(gdir, "g1", SCHOLAR)
    _game(gdir, "live", [], fen=_fen(SCHOLAR[:3]))
    real = diagnosis._one_game

    def one_game(pgn, *args, **kwargs):
        outcome = real(pgn, *args, **kwargs)
        if pgn.stem == "g1":  # refused, then the live game ends before the wait is noted
            GameState.load("live", games_dir=gdir).resign(by="white")
        return outcome

    monkeypatch.setattr(diagnosis, "_one_game", one_game)
    memory = RoundMemory()
    assert run_once(gdir, engine_path="/fake", memory=memory, now=NOW)["waiting"] == ["g1"]
    monkeypatch.setattr(diagnosis, "_one_game", real)
    later = run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 30)
    assert "g1" in later["diagnosed"]


def test_a_wait_on_no_known_game_falls_back_on_the_delay(gdir: Path) -> None:
    _game(gdir, "g1", SCHOLAR)
    memory = RoundMemory()
    pgn = gdir / "g1.pgn"
    memory.wait_for(pgn, Wait.on(None), {pgn: pgn.stat().st_mtime_ns}, NOW)
    assert memory.blocked == {} and memory.deferred("g1", NOW + RETRY_SECONDS - 1)
    assert not memory.deferred("g1", NOW + RETRY_SECONDS)


def test_a_blocking_game_gone_falls_back_on_the_delay(gdir: Path) -> None:
    _game(gdir, "g1", SCHOLAR)
    memory = RoundMemory()
    pgn = gdir / "g1.pgn"
    memory.wait_for(pgn, Wait.on("vanished"), {pgn: pgn.stat().st_mtime_ns}, NOW)
    assert memory.blocked == {} and memory.deferred("g1", NOW + 1)


def test_without_memory_nothing_is_deferred(gdir: Path, popen, monkeypatch) -> None:
    _game(gdir, "g1", SCHOLAR)
    monkeypatch.setattr(diagnosis, "diagnose", lambda *a, **k: 1 / 0)
    run_once(gdir, engine_path="/fake", now=NOW)
    assert run_once(gdir, engine_path="/fake", now=NOW + 1)["failed"] == ["g1"]


# --- stop between games ---------------------------------------------------------------


def test_stop_ends_the_round_after_the_game_in_hand(gdir: Path, popen, monkeypatch) -> None:
    _game(gdir, "g1", SCHOLAR)
    _game(gdir, "g2", SCHOLAR)
    stop = threading.Event()
    real = diagnosis._one_game
    handled: list[str] = []

    def one_game(pgn, *args, **kwargs):
        handled.append(pgn.stem)
        outcome = real(pgn, *args, **kwargs)
        stop.set()  # SIGTERM during the first game
        return outcome

    monkeypatch.setattr(diagnosis, "_one_game", one_game)
    memory = RoundMemory()
    summary = run_once(gdir, engine_path="/fake", memory=memory, now=NOW, stop=stop)
    assert summary["diagnosed"] == ["g1"] and handled == ["g1"]
    assert read_report(gdir, "g1") is not None and read_report(gdir, "g2") is None
    assert "g2" not in memory.settled  # left for the next start
    monkeypatch.setattr(diagnosis, "_one_game", real)
    later = run_once(gdir, engine_path="/fake", memory=memory, now=NOW + 30)
    assert later["diagnosed"] == ["g2"]


def test_a_stop_set_before_the_round_reads_nothing(gdir: Path, popen, loads) -> None:
    _game(gdir, "g1", SCHOLAR)
    stop = threading.Event()
    stop.set()
    loads.clear()
    assert run_once(gdir, engine_path="/fake", stop=stop)["diagnosed"] == []
    assert loads == []
    popen.assert_not_called()


# --- the loop -------------------------------------------------------------------------


def test_the_loop_makes_rounds_until_stopped(gdir: Path, monkeypatch) -> None:
    stop = threading.Event()
    rounds: list[RoundMemory] = []
    stops: list[threading.Event | None] = []

    def one_round(games_dir, **kwargs):
        rounds.append(kwargs["memory"])
        stops.append(kwargs.get("stop"))
        if len(rounds) == 1:
            raise registry.RegistryError("unreadable")  # a failing round : the next comes
        if len(rounds) == 3:
            stop.set()
        return {"diagnosed": [], "waiting": [], "failed": [], "deferred": [], "exercises": 0}

    monkeypatch.setattr(diagnosis, "run_once", one_round)
    run_forever(gdir, interval=0, stop=stop)
    assert len(rounds) == 3
    assert rounds[0] is rounds[1] is rounds[2]  # one memory across rounds
    assert stops == [stop, stop, stop]  # read between two games too


def test_the_cli_runs_until_sigterm(gdir: Path, monkeypatch) -> None:
    handlers = {}
    monkeypatch.setattr(diagnosis.signal, "signal", lambda sig, h: handlers.setdefault(sig, h))
    seen = {}

    def fake_run(games_dir, *, interval, depth, stop):
        seen.update(games_dir=games_dir, interval=interval, depth=depth)
        handlers[diagnosis.signal.SIGTERM](15, None)  # SIGTERM arrives
        seen["stopped"] = stop.is_set()

    monkeypatch.setattr(diagnosis, "run_forever", fake_run)
    argv = ["run", "--games-dir", str(gdir), "--interval", "5", "--depth", "10"]
    assert diagnosis.main(argv) == 0
    assert seen == {"games_dir": gdir, "interval": 5.0, "depth": 10, "stopped": True}
