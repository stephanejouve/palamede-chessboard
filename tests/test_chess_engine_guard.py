"""Tests for ``palamede_chessboard.chess_engine_guard``."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import chess
import pytest

from palamede_chessboard.chess_engine_guard import (
    GUARDED_TOOLS,
    MASKED_CLASSIFICATION,
    OPENING_PLIES_IGNORED,
    _mirror_fingerprint,
    check_engine_call,
    engine_calls_log_path,
    log_engine_call,
    mask_walk_entries,
    normalize_fen_for_match,
)
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl


def _seed(tmp_path: Path, game_id: str = "live") -> GameState:
    meta = GameMetadata(
        game_id=game_id,
        event="Test",
        site="pytest",
        round="1",
        white="junior",
        black="leader",
        arbiter="admin",
        spectators=[],
        time_control=TimeControl(format="unrated"),
    )
    return GameState.create(meta, games_dir=tmp_path)


def _play_sicilian(state: GameState, extra_plies: list[str] | None = None) -> None:
    """Push 10 plies of shared Sicilian Najdorf then optional divergence.

    10 plies sits entirely inside the opening threshold
    (:data:`OPENING_PLIES_IGNORED` = 12). Callers that want to produce
    post-opening positions must feed ``extra_plies`` ; a divergence at
    ply 11 makes two games differ at every post-opening ply onwards.
    """
    base = [
        "e4",
        "c5",  # 1-2
        "Nf3",
        "d6",  # 3-4
        "d4",
        "cxd4",  # 5-6
        "Nxd4",
        "Nf6",  # 7-8
        "Nc3",
        "a6",  # 9-10 (last shared ply, still opening)
    ]
    for san in base:
        state.play_move(san)
    for san in extra_plies or []:
        state.play_move(san)


class TestNormalizeFen:
    def test_keeps_placement_and_side(self) -> None:
        fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        assert normalize_fen_for_match(fen) == ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w")

    def test_strips_counters_and_rights(self) -> None:
        a = "8/8/8/8/8/8/8/4K2k w - - 0 1"
        b = "8/8/8/8/8/8/8/4K2k w - - 99 42"
        assert normalize_fen_for_match(a) == normalize_fen_for_match(b)

    def test_distinguishes_side_to_move(self) -> None:
        white = normalize_fen_for_match("8/8/8/8/8/8/8/4K2k w - - 0 1")
        black = normalize_fen_for_match("8/8/8/8/8/8/8/4K2k b - - 0 1")
        assert white != black

    def test_invalid_returns_none(self) -> None:
        assert normalize_fen_for_match("not a fen") is None
        assert normalize_fen_for_match("") is None
        assert normalize_fen_for_match("8/8/8/8/8/8/8/8 x - - 0 1") is None


class TestMirrorFingerprint:
    def test_mirror_flips_color_and_board(self) -> None:
        # 1.e4 played, black to move.
        board = chess.Board()
        board.push_san("e4")
        fp = normalize_fen_for_match(board.fen())
        assert fp is not None
        mirror = _mirror_fingerprint(fp)
        assert mirror is not None
        assert mirror != fp
        # Mirror is a valid position with White to move after being flipped.
        placement, side = mirror.split()
        assert side == "w"
        # Re-mirroring returns to the original (involution).
        back = _mirror_fingerprint(mirror)
        assert back == fp

    def test_mirror_of_invalid_is_none(self) -> None:
        assert _mirror_fingerprint("not a fingerprint") is None


class TestCheckEngineCallGameId:
    def test_in_progress_refused(self, tmp_path: Path) -> None:
        _seed(tmp_path, "live")
        decision = check_engine_call(tmp_path, tool="chess_analyze_position", game_id="live")
        assert decision["allowed"] is False
        assert decision["matched_game_id"] == "live"
        assert "partie en cours" in decision["reason"]

    def test_finished_game_without_fingerprint_match_allowed(self, tmp_path: Path) -> None:
        state = _seed(tmp_path, "done")
        state.resign(by="white")
        decision = check_engine_call(tmp_path, tool="chess_analyze_position", game_id="done")
        assert decision["allowed"] is True

    def test_missing_game_allowed(self, tmp_path: Path) -> None:
        decision = check_engine_call(
            tmp_path, tool="chess_analyze_position", game_id="never-existed"
        )
        assert decision["allowed"] is True


class TestOpeningThreshold:
    """Positions reached in the first OPENING_PLIES_IGNORED plies stay allowed."""

    def test_opening_position_of_in_progress_not_matched(self, tmp_path: Path) -> None:
        state = _seed(tmp_path, "live")
        _play_sicilian(state, extra_plies=["Be3", "e5", "Nb3", "Be6"])
        # Reconstruct the position after 1.e4 c5 (ply 2) — pure opening.
        replay = chess.Board()
        replay.push_san("e4")
        replay.push_san("c5")
        decision = check_engine_call(tmp_path, tool="chess_analyze_position", fen=replay.fen())
        assert decision["allowed"] is True

    def test_post_opening_position_is_matched(self, tmp_path: Path) -> None:
        state = _seed(tmp_path, "live")
        _play_sicilian(state, extra_plies=["Be3", "e5", "Nb3", "Be6"])
        # Current live position = ply 14, past the threshold.
        decision = check_engine_call(tmp_path, tool="chess_analyze_position", fen=state.board.fen())
        assert decision["allowed"] is False
        assert decision["matched_game_id"] == "live"


class TestPgnCloneAttack:
    """CD 2026-10-03 test 2.a : cloning the PGN with a flipped Result."""

    def test_cloned_game_with_fake_result_still_fingerprinted(self, tmp_path: Path) -> None:
        state = _seed(tmp_path, "live")
        _play_sicilian(state, extra_plies=["Be3", "e5", "Nb3", "Be6"])  # ply 14

        # Build the clone directly on disk : same PGN, result = "1-0".
        pgn_live = (tmp_path / "live.pgn").read_text(encoding="utf-8")
        clone = pgn_live.replace('[Result "*"]', '[Result "1-0"]')
        (tmp_path / "clone.pgn").write_text(clone, encoding="utf-8")

        # chess_analyze_position on the clone's current position must refuse.
        clone_state = GameState.load("clone", games_dir=tmp_path)
        assert clone_state.metadata.result == "1-0"
        decision = check_engine_call(
            tmp_path,
            tool="chess_analyze_position",
            game_id="clone",
            ply=clone_state.board.ply(),
        )
        assert decision["allowed"] is False
        assert decision["matched_game_id"] == "live"

    def test_cloned_game_opening_plies_allowed(self, tmp_path: Path) -> None:
        """Even a clone must allow analysis of the shared opening ply range."""
        state = _seed(tmp_path, "live")
        _play_sicilian(state, extra_plies=["Be3", "e5", "Nb3", "Be6"])
        pgn_live = (tmp_path / "live.pgn").read_text(encoding="utf-8")
        clone = pgn_live.replace('[Result "*"]', '[Result "1-0"]')
        (tmp_path / "clone.pgn").write_text(clone, encoding="utf-8")
        decision = check_engine_call(
            tmp_path,
            tool="chess_analyze_position",
            game_id="clone",
            ply=2,  # opening
        )
        assert decision["allowed"] is True


class TestFenMirrorAttack:
    def test_mirrored_fen_still_matches(self, tmp_path: Path) -> None:
        state = _seed(tmp_path, "live")
        _play_sicilian(state, extra_plies=["Be3", "e5", "Nb3", "Be6"])
        mirrored = chess.Board(state.board.fen()).mirror().fen()
        decision = check_engine_call(tmp_path, tool="chess_analyze_position", fen=mirrored)
        assert decision["allowed"] is False
        assert decision["matched_game_id"] == "live"


class TestUnrelatedPositionsAllowed:
    def test_random_endgame_fen_allowed(self, tmp_path: Path) -> None:
        state = _seed(tmp_path, "live")
        _play_sicilian(state, extra_plies=["Be3", "e5"])
        unrelated = "8/8/8/8/8/8/4K2k/5Q2 w - - 0 1"
        decision = check_engine_call(tmp_path, tool="chess_analyze_position", fen=unrelated)
        assert decision["allowed"] is True

    def test_unrelated_finished_game_allowed(self, tmp_path: Path) -> None:
        """GM game shares the opening but diverges past the threshold."""
        state_live = _seed(tmp_path, "live")
        _play_sicilian(state_live, extra_plies=["Be3", "e5", "Nb3", "Be6"])
        # GM diverges at ply 11 (h3 vs Be3). Both games are at ply 14 but
        # every post-opening position differs — the guard must stay out.
        gm = _seed(tmp_path, "gm-game")
        _play_sicilian(gm, extra_plies=["h3", "e6", "f3", "Be7"])
        gm.resign(by="black")
        decision = check_engine_call(
            tmp_path, tool="chess_analyze_position", game_id="gm-game", ply=14
        )
        assert decision["allowed"] is True


class TestUnguardedTool:
    def test_board_facts_bypasses_guard(self, tmp_path: Path) -> None:
        _seed(tmp_path, "live")
        decision = check_engine_call(tmp_path, tool="chess_board_facts", game_id="live")
        assert decision["allowed"] is True

    def test_guarded_tools_set(self) -> None:
        assert "chess_analyze_position" in GUARDED_TOOLS
        assert "chess_analyze_game" in GUARDED_TOOLS
        assert "chess_get_my_mistakes" in GUARDED_TOOLS
        assert "chess_board_facts" not in GUARDED_TOOLS


class TestMaskWalkEntries:
    """CD 2026-10-03 : per-ply masking preserves Sicilian post-mortem."""

    @dataclass
    class _FakeWalkEntry:
        ply: int
        mover: str
        san: str
        fen_before: str
        fen_after: str
        score_cp_before: int | None
        score_cp_after: int | None
        cp_loss: int | None
        classification: str

    def _walk_from_state(self, state: GameState) -> list[_FakeWalkEntry]:
        walk: list = []
        board = chess.Board()
        for i, san in enumerate(state.san_history, start=1):
            fen_before = board.fen()
            move = board.parse_san(san)
            board.push(move)
            walk.append(
                self._FakeWalkEntry(
                    ply=i,
                    mover="white" if i % 2 == 1 else "black",
                    san=san,
                    fen_before=fen_before,
                    fen_after=board.fen(),
                    score_cp_before=10,
                    score_cp_after=12,
                    cp_loss=0,
                    classification="good",
                )
            )
        return walk

    def test_live_clone_walk_masks_post_opening_plies(self, tmp_path: Path) -> None:
        live = _seed(tmp_path, "live")
        _play_sicilian(live, extra_plies=["Be3", "e5", "Nb3", "Be6"])
        clone = _seed(tmp_path, "clone")
        _play_sicilian(clone, extra_plies=["Be3", "e5", "Nb3", "Be6"])
        clone.resign(by="black")
        walk = self._walk_from_state(clone)
        masked, count, details = mask_walk_entries(walk, tmp_path)
        assert count > 0
        for entry in masked:
            if entry.classification == MASKED_CLASSIFICATION:
                assert entry.ply >= OPENING_PLIES_IGNORED
                assert entry.score_cp_before is None
                assert entry.score_cp_after is None
                assert entry.cp_loss is None
        assert all(d["matched_game_id"] == "live" for d in details)

    def test_opening_plies_never_masked(self, tmp_path: Path) -> None:
        live = _seed(tmp_path, "live")
        _play_sicilian(live, extra_plies=["Be3", "e5"])
        clone = _seed(tmp_path, "clone")
        _play_sicilian(clone, extra_plies=["Be3", "e5"])
        clone.resign(by="black")
        walk = self._walk_from_state(clone)
        masked, _, _ = mask_walk_entries(walk, tmp_path)
        for entry in masked:
            if entry.ply < OPENING_PLIES_IGNORED:
                assert entry.classification != MASKED_CLASSIFICATION

    def test_gm_game_with_divergence_not_masked(self, tmp_path: Path) -> None:
        """A game that shares the opening but diverges stays fully analysable."""
        live = _seed(tmp_path, "live")
        _play_sicilian(live, extra_plies=["Be3", "e5", "Nb3", "Be6"])
        gm = _seed(tmp_path, "gm")
        _play_sicilian(gm, extra_plies=["h3", "e6", "f3", "Be7"])
        gm.resign(by="black")
        walk = self._walk_from_state(gm)
        _, count, _ = mask_walk_entries(walk, tmp_path)
        # The two games share plies up to 14 (same Sicilian); from ply 15
        # the lines diverge, so no post-opening match. The guard must
        # leave the GM walk unmasked.
        assert count == 0

    def test_no_in_progress_games_no_masking(self, tmp_path: Path) -> None:
        done = _seed(tmp_path, "done")
        _play_sicilian(done, extra_plies=["Be3", "e5"])
        done.resign(by="white")
        walk = self._walk_from_state(done)
        masked, count, _ = mask_walk_entries(walk, tmp_path)
        assert count == 0
        assert all(e.classification != MASKED_CLASSIFICATION for e in masked)


class TestLogEngineCall:
    def test_log_writes_one_line_per_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_path = tmp_path / "engine_calls_log.jsonl"
        monkeypatch.setenv("OUTILLAGES_ENGINE_CALLS_LOG", str(log_path))
        assert engine_calls_log_path() == log_path

        log_engine_call(
            tool="chess_analyze_position",
            decision={"allowed": False, "matched_game_id": "live", "reason": "test"},
            game_id="live",
            caller="junior",
        )
        log_engine_call(
            tool="chess_analyze_position",
            decision={"allowed": True, "matched_game_id": None, "reason": None},
            fen="8/8/8/8/8/8/4K2k/5Q2 w - - 0 1",
            caller="admin",
        )
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        entries = [json.loads(line) for line in lines]
        assert entries[0]["decision"] == "refused"
        assert entries[0]["matched_game_id"] == "live"
        assert entries[0]["caller"] == "junior"
        assert entries[1]["decision"] == "allowed"
        assert entries[1]["fen"] is not None
        assert entries[1]["caller"] == "admin"

    def test_log_concurrent_writes_do_not_interleave(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CD nit 2026-10-03 : four agents writing in parallel must not shred lines."""
        log_path = tmp_path / "concurrent.jsonl"
        monkeypatch.setenv("OUTILLAGES_ENGINE_CALLS_LOG", str(log_path))

        writers = 8
        per_writer = 25

        def _hammer(writer_id: int) -> None:
            for i in range(per_writer):
                log_engine_call(
                    tool="chess_analyze_position",
                    decision={
                        "allowed": True,
                        "matched_game_id": None,
                        "reason": None,
                    },
                    fen="8/8/8/8/8/8/4K2k/5Q2 w - - 0 1",
                    caller=f"agent-{writer_id}-call-{i}",
                )

        threads = [threading.Thread(target=_hammer, args=(wid,)) for wid in range(writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        content = log_path.read_text(encoding="utf-8").splitlines()
        assert len(content) == writers * per_writer
        for line in content:
            # Every line must be valid JSON — flock guarantees atomicity.
            payload = json.loads(line)
            assert payload["caller"].startswith("agent-")

    def test_log_accepts_extra_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_path = tmp_path / "extra.jsonl"
        monkeypatch.setenv("OUTILLAGES_ENGINE_CALLS_LOG", str(log_path))
        log_engine_call(
            tool="chess_analyze_game",
            decision={"allowed": True, "matched_game_id": None, "reason": None},
            game_id="done",
            caller="junior",
            extra={"masked_plies": [{"ply": 15, "matched_game_id": "live"}]},
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["masked_plies"] == [{"ply": 15, "matched_game_id": "live"}]


def test_fen_match_still_refused_when_game_id_invalid(tmp_path: Path) -> None:
    """Fix palamede#19 : game_id ghost + fen d'une partie en cours → refusé.

    Un caller fournissant un ``game_id`` inexistant combiné à un
    ``fen`` matchant une partie en cours ne doit PAS bypass le
    garde-fou. Le bug pré-existant (hérité d'outillages) faisait
    short-circuit sur ``GameNotFoundError`` et retournait
    ``_ALLOWED`` sans contrôler le ``fen``. Fix BT-004 commit 1 :
    ``_check_against_game_id`` retourne ``None`` sur
    ``GameNotFoundError`` → fall-through au check ``fen``.
    """
    # Seed one in-progress game past opening (ply ≥ 12).
    live = _seed(tmp_path, "live")
    _play_sicilian(live, extra_plies=["Be2", "e5", "Nb3"])  # ply 13
    live_fen = live.board.fen()

    verdict = check_engine_call(
        tmp_path,
        tool="chess_analyze_position",
        game_id="nexiste-pas",
        fen=live_fen,
    )
    assert verdict["allowed"] is False
    assert verdict["matched_game_id"] == "live"
