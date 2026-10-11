"""Tests for ``palamede_chessboard.chessboard_state``."""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.chessboard_state import (
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


def _meta(game_id: str = "test-game") -> GameMetadata:
    return GameMetadata(
        game_id=game_id,
        event="Test",
        site="pytest",
        round="1",
        white="white-player",
        black="black-player",
        arbiter="arbiter-x",
        spectators=["s1", "s2"],
        time_control=TimeControl(format="5+3", start_seconds=300, increment_seconds=3),
    )


class TestCreateLoad:
    def test_create_and_load_roundtrip(self, tmp_path: Path) -> None:
        state = GameState.create(_meta(), games_dir=tmp_path)
        loaded = GameState.load("test-game", games_dir=tmp_path)
        assert loaded.metadata.white == "white-player"
        assert loaded.metadata.arbiter == "arbiter-x"
        assert loaded.metadata.spectators == ["s1", "s2"]
        assert loaded.metadata.time_control.format == "5+3"
        assert loaded.board.fen() == state.board.fen()

    def test_create_rejects_existing(self, tmp_path: Path) -> None:
        GameState.create(_meta(), games_dir=tmp_path)
        with pytest.raises(GameAlreadyExistsError):
            GameState.create(_meta(), games_dir=tmp_path)

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(GameNotFoundError):
            GameState.load("no-such-game", games_dir=tmp_path)

    @pytest.mark.parametrize("bad_id", ["", "../escape", "spaces here", "x" * 70])
    def test_invalid_game_id_rejected(self, bad_id: str, tmp_path: Path) -> None:
        with pytest.raises(ChessboardError):
            GameState.create(_meta(bad_id), games_dir=tmp_path)


class TestPlayMove:
    def test_legal_move_pushed(self, tmp_path: Path) -> None:
        state = GameState.create(_meta(), games_dir=tmp_path)
        result = state.play_move("e4")
        assert result["san"] == "e4"
        assert result["ply"] == 1
        assert "e2e4" in state.board.fen() or state.board.peek().uci() == "e2e4"
        assert state.san_history == ["e4"]

    def test_illegal_move_rejected(self, tmp_path: Path) -> None:
        state = GameState.create(_meta(), games_dir=tmp_path)
        with pytest.raises(IllegalMoveError):
            state.play_move("Ke5")  # roi blanc ne peut pas teleporter

    def test_illegal_move_does_not_persist(self, tmp_path: Path) -> None:
        state = GameState.create(_meta(), games_dir=tmp_path)
        try:
            state.play_move("Qxd8")  # roi cloue + non valide en position initiale
        except IllegalMoveError:
            pass
        assert state.san_history == []
        reload = GameState.load("test-game", games_dir=tmp_path)
        assert reload.san_history == []

    def test_round2_blocked_diagonal_caught(self, tmp_path: Path) -> None:
        """Regression Round-2 : the 20.Qxd3 erratum that slipped past the
        manual arbiter would be rejected by parse_san. Drive the position
        from a FEN to keep the test compact."""
        state = GameState.create(
            _meta("regression-r2-blocked"),
            games_dir=tmp_path,
            starting_fen="r2qkb1r/5pp1/p7/1ppPp3/7P/3bBQ2/PPP5/1K1R3R w kq - 0 20",
        )
        with pytest.raises(IllegalMoveError):
            state.play_move("Qxd3")

    def test_checkmate_sets_result(self, tmp_path: Path) -> None:
        # Fool's mate from White's POV
        state = GameState.create(_meta("fools"), games_dir=tmp_path)
        state.play_move("f3")
        state.play_move("e5")
        state.play_move("g4")
        state.play_move("Qh4#")
        assert state.metadata.result == "0-1"
        assert state.board.is_checkmate()


class TestExpectedPly:
    """Idempotence guard for ``play_move(expected_ply=…)`` (chess-autoplay #270)."""

    def test_expected_ply_match_succeeds(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("exp-match"), games_dir=tmp_path)
        # ply() starts at 0 on a fresh board
        result = state.play_move("e4", expected_ply=0)
        assert result["san"] == "e4"
        assert result["ply"] == 1

    def test_expected_ply_mismatch_raises(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("exp-stale"), games_dir=tmp_path)
        state.play_move("e4")  # advances to ply 1
        with pytest.raises(StalePlyError) as exc_info:
            # Caller thought it was still ply 0 — concurrent writer advanced.
            state.play_move("e5", expected_ply=0)
        assert exc_info.value.current_ply == 1
        assert exc_info.value.expected_ply == 0
        # Mismatch MUST abort before mutation.
        assert state.san_history == ["e4"]
        reload = GameState.load("exp-stale", games_dir=tmp_path)
        assert reload.san_history == ["e4"]

    def test_expected_ply_none_backward_compat(self, tmp_path: Path) -> None:
        """Default (``expected_ply=None``) skips the check — old callers unchanged."""
        state = GameState.create(_meta("exp-compat"), games_dir=tmp_path)
        state.play_move("e4")  # no kwarg
        state.play_move("e5", by="black")  # explicit None equivalent
        assert state.san_history == ["e4", "e5"]

    def test_expected_ply_mismatch_after_game_over(self, tmp_path: Path) -> None:
        """Fool's mate ends the game at ply 4 ; a stale caller still at ply 2
        gets STALE_PLY (not an "already over" error) — the ply guard fires
        first so the autoplay daemon can detect divergence vs end-state."""
        state = GameState.create(_meta("exp-gameover"), games_dir=tmp_path)
        state.play_move("f3")
        state.play_move("e5")
        state.play_move("g4")
        state.play_move("Qh4#")
        assert state.board.is_checkmate()
        with pytest.raises(StalePlyError) as exc_info:
            state.play_move("d4", expected_ply=2)
        assert exc_info.value.current_ply == 4
        assert exc_info.value.expected_ply == 2


class TestPgnPersistence:
    def test_save_writes_headers_and_moves(self, tmp_path: Path) -> None:
        state = GameState.create(_meta(), games_dir=tmp_path)
        state.play_move("e4")
        state.play_move("e5")
        pgn = state.raw_pgn()
        assert "[White " in pgn
        assert "[Arbiter " in pgn
        assert "1. e4 e5" in pgn

    def test_to_state_dict_compact(self, tmp_path: Path) -> None:
        state = GameState.create(_meta(), games_dir=tmp_path)
        state.play_move("e4")
        snap = state.to_state_dict()
        assert snap["game_id"] == "test-game"
        assert snap["ply"] == 1
        assert snap["turn"] == "black"
        assert snap["san_history"] == ["e4"]


class TestResign:
    """#244 : chess_resign — a player concedes without checkmate/stalemate."""

    def test_white_resign_sets_result_zero_one(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("wr"), games_dir=tmp_path)
        state.play_move("e4")
        state.play_move("e5")
        result = state.resign(by="white")
        assert state.metadata.result == "0-1"
        assert result["result"] == "0-1"
        assert result["resigned_by"] == "white"
        assert state.metadata.termination_note == "white-player resigns"

    def test_black_resign_sets_result_one_zero(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("br"), games_dir=tmp_path)
        state.resign(by="black")
        assert state.metadata.result == "1-0"

    def test_resign_by_player_name(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("byname"), games_dir=tmp_path)
        state.resign(by="black-player")  # matches metadata.black
        assert state.metadata.result == "1-0"

    def test_resign_rejects_after_game_over(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("done"), games_dir=tmp_path)
        state.resign(by="white")
        with pytest.raises(InvalidGameStateError):
            state.resign(by="black")

    def test_resign_after_checkmate_rejected(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("mate-then-resign"), games_dir=tmp_path)
        state.play_move("f3")
        state.play_move("e5")
        state.play_move("g4")
        state.play_move("Qh4#")
        assert state.metadata.result == "0-1"
        with pytest.raises(InvalidGameStateError):
            state.resign(by="white")

    def test_resign_rewrites_pgn_headers(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("pgnres"), games_dir=tmp_path)
        state.play_move("e4")
        state.resign(by="black")
        pgn = state.raw_pgn()
        assert '[Result "1-0"]' in pgn
        assert '[Termination "black-player resigns"]' in pgn
        assert "{black-player resigns} 1-0" in pgn

    def test_resign_clears_pending_draw_offer(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("resdo"), games_dir=tmp_path)
        state.offer_draw(by="white")
        assert state.metadata.draw_offer is not None
        state.resign(by="white")
        assert state.metadata.draw_offer is None

    def test_resign_unresolvable_by_raises(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("nores"), games_dir=tmp_path)
        with pytest.raises(ChessboardError):
            state.resign(by="ghost")

    def test_resign_roundtrip_persists(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("persist"), games_dir=tmp_path)
        state.play_move("e4")
        state.resign(by="white")
        reloaded = GameState.load("persist", games_dir=tmp_path)
        assert reloaded.metadata.result == "0-1"
        assert reloaded.metadata.termination_note == "white-player resigns"


class TestDrawOffer:
    """#244 : chess_offer_draw / chess_agree_draw / chess_decline_draw."""

    def test_offer_records_pending(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("do1"), games_dir=tmp_path)
        state.play_move("e4")  # ply becomes 1
        result = state.offer_draw(by="black")
        assert result["offered_by"] == "black"
        assert result["ply"] == 1
        assert state.metadata.draw_offer == {"by": "black", "ply": 1}

    def test_offer_idempotent_same_side(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("do2"), games_dir=tmp_path)
        state.offer_draw(by="white")
        second = state.offer_draw(by="white")
        assert second["idempotent"] is True
        assert state.metadata.draw_offer["by"] == "white"

    def test_offer_from_opposite_side_when_pending_rejected(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("do3"), games_dir=tmp_path)
        state.offer_draw(by="white")
        with pytest.raises(InvalidGameStateError):
            state.offer_draw(by="black")

    def test_agree_ends_game_half_half(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("ag1"), games_dir=tmp_path)
        state.play_move("e4")
        state.offer_draw(by="white")
        result = state.agree_draw(by="black")
        assert state.metadata.result == "1/2-1/2"
        assert result["result"] == "1/2-1/2"
        assert result["offered_by"] == "white"
        assert result["agreed_by"] == "black"
        assert state.metadata.draw_offer is None
        assert state.metadata.termination_note == "draw agreed"

    def test_agree_without_offer_rejected(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("ag2"), games_dir=tmp_path)
        with pytest.raises(InvalidGameStateError):
            state.agree_draw(by="black")

    def test_agree_same_side_rejected(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("ag3"), games_dir=tmp_path)
        state.offer_draw(by="white")
        with pytest.raises(InvalidGameStateError):
            state.agree_draw(by="white")

    def test_decline_clears_offer_and_game_continues(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("dc1"), games_dir=tmp_path)
        state.offer_draw(by="white")
        result = state.decline_draw(by="black")
        assert result["declined_by"] == "black"
        assert result["offered_by"] == "white"
        assert state.metadata.draw_offer is None
        assert state.metadata.result == "*"

    def test_decline_same_side_rejected(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("dc2"), games_dir=tmp_path)
        state.offer_draw(by="white")
        with pytest.raises(InvalidGameStateError):
            state.decline_draw(by="white")

    def test_playing_move_implicitly_declines_opponent_offer(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("imp"), games_dir=tmp_path)
        state.play_move("e4")  # white plays, black to move
        state.offer_draw(by="white")  # white offers, black hasn't answered
        state.play_move("e5")  # black plays instead — implicit decline
        assert state.metadata.draw_offer is None
        assert state.metadata.result == "*"

    def test_own_offer_not_cleared_on_own_next_move(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("keep"), games_dir=tmp_path)
        state.play_move("e4")  # white plays
        state.offer_draw(by="black")  # black offers on black's move
        # Simulate black playing (which shouldn't happen — they'd wait)
        # but the key invariant is that only the *opposite* side's offer is cleared.
        state.play_move("e5")  # black plays — same side, offer stays
        assert state.metadata.draw_offer == {"by": "black", "ply": 1}

    def test_agree_rewrites_pgn(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("agpgn"), games_dir=tmp_path)
        state.play_move("e4")
        state.offer_draw(by="white")
        state.agree_draw(by="black")
        pgn = state.raw_pgn()
        assert '[Result "1/2-1/2"]' in pgn
        assert '[Termination "draw agreed"]' in pgn
        assert "{draw agreed} 1/2-1/2" in pgn
        assert "DrawOffer" not in pgn  # cleared on agree

    def test_pending_offer_persists_across_reload(self, tmp_path: Path) -> None:
        state = GameState.create(_meta("preload"), games_dir=tmp_path)
        state.play_move("e4")
        state.offer_draw(by="black")
        reloaded = GameState.load("preload", games_dir=tmp_path)
        assert reloaded.metadata.draw_offer == {"by": "black", "ply": 1}


class TestCommentary:
    def test_post_then_read(self, tmp_path: Path) -> None:
        post_commentary(
            "test-game",
            ply=3,
            source="arbiter",
            text="bonne position",
            games_dir=tmp_path,
        )
        post_commentary(
            "test-game",
            ply=3,
            source="spectator",
            text="meh",
            games_dir=tmp_path,
        )
        post_commentary(
            "test-game",
            ply=4,
            source="arbiter",
            text="meilleure",
            games_dir=tmp_path,
        )
        all_entries = get_commentary("test-game", games_dir=tmp_path)
        ply3 = get_commentary("test-game", ply=3, games_dir=tmp_path)
        assert len(all_entries) == 3
        assert len(ply3) == 2
        assert {e["source"] for e in ply3} == {"arbiter", "spectator"}

    def test_get_unknown_game_empty(self, tmp_path: Path) -> None:
        assert get_commentary("never-created", games_dir=tmp_path) == []


class TestListGames:
    def _seed(self, tmp_path: Path, game_id: str, white: str, black: str) -> GameState:
        meta = GameMetadata(
            game_id=game_id,
            event="Test",
            site="pytest",
            round="1",
            white=white,
            black=black,
            arbiter="arbiter-x",
            spectators=[],
            time_control=TimeControl(format="unrated"),
        )
        return GameState.create(meta, games_dir=tmp_path)

    def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        assert list_games(tmp_path) == []

    def test_missing_dir_returns_empty_list(self, tmp_path: Path) -> None:
        assert list_games(tmp_path / "does-not-exist") == []

    def test_entry_shape(self, tmp_path: Path) -> None:
        self._seed(tmp_path, "g1", "junior", "leader")
        entries = list_games(tmp_path)
        assert len(entries) == 1
        e = entries[0]
        assert set(e.keys()) == {
            "game_id",
            "white",
            "black",
            "result",
            "ply",
            "turn",
            "is_game_over",
            "mtime",
        }
        assert e["game_id"] == "g1"
        assert e["white"] == "junior"
        assert e["black"] == "leader"
        assert e["result"] == "*"
        assert e["ply"] == 0
        assert e["turn"] == "white"
        assert e["is_game_over"] is False

    def test_turn_reflects_board_state(self, tmp_path: Path) -> None:
        state = self._seed(tmp_path, "g1", "junior", "leader")
        state.play_move("e4")
        entries = list_games(tmp_path)
        assert entries[0]["ply"] == 1
        assert entries[0]["turn"] == "black"

    def test_finished_game_flagged(self, tmp_path: Path) -> None:
        state = self._seed(tmp_path, "g1", "junior", "leader")
        state.resign(by="white")
        entries = list_games(tmp_path)
        assert entries[0]["result"] == "0-1"
        assert entries[0]["is_game_over"] is True

    def test_sorted_by_mtime_desc(self, tmp_path: Path) -> None:
        import os

        self._seed(tmp_path, "older", "a", "b")
        older_pgn = tmp_path / "older.pgn"
        os.utime(older_pgn, (1_000_000, 1_000_000))
        self._seed(tmp_path, "newer", "c", "d")
        newer_pgn = tmp_path / "newer.pgn"
        os.utime(newer_pgn, (2_000_000, 2_000_000))
        ids = [e["game_id"] for e in list_games(tmp_path)]
        assert ids == ["newer", "older"]

    def test_player_filter_matches_either_side(self, tmp_path: Path) -> None:
        self._seed(tmp_path, "g1", "junior", "leader")
        self._seed(tmp_path, "g2", "admin", "leader")
        self._seed(tmp_path, "g3", "admin", "cd")
        ids = {e["game_id"] for e in list_games(tmp_path, player="leader")}
        assert ids == {"g1", "g2"}

    def test_player_filter_case_insensitive(self, tmp_path: Path) -> None:
        self._seed(tmp_path, "g1", "Junior", "Leader")
        assert len(list_games(tmp_path, player="JUNIOR")) == 1
        assert len(list_games(tmp_path, player="junior")) == 1

    def test_active_only_drops_finished(self, tmp_path: Path) -> None:
        self._seed(tmp_path, "live", "a", "b")
        done = self._seed(tmp_path, "done", "a", "b")
        done.resign(by="white")
        ids = [e["game_id"] for e in list_games(tmp_path, active_only=True)]
        assert ids == ["live"]

    def test_malformed_pgn_skipped(self, tmp_path: Path) -> None:
        self._seed(tmp_path, "ok", "a", "b")
        # Non-UTF-8 bytes trigger a decode error inside GameState.load,
        # which list_games must swallow rather than propagate.
        (tmp_path / "broken.pgn").write_bytes(b"\xff\xfe\x00not-utf-8-pgn")
        ids = [e["game_id"] for e in list_games(tmp_path)]
        assert ids == ["ok"]
