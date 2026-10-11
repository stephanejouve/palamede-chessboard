"""Pairing-logic tests : optimal pairing, bye selection, rematch penalty.

Covers the combinatorial core of the matchmaker — brute-force
minimum-sum-of-gaps perfect matching, deterministic lex tie-break,
odd-count bye-the-newcomer rule, and the "no immediate rematch"
penalty introduced in CD review 2026-10-06 PR #297 cycle 3.
"""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.chess_elo import RatedGame, RatingBoard
from palamede_chessboard.chess_matchmaking import (
    REMATCH_PENALTY_CP,
    _choose_bye,
    _last_round_pairs,
    _optimal_pairing,
    schedule_round,
)
from tests.chess_matchmaking.conftest import _board, _board_with_games, _write_pgn


class TestOptimalPairing:
    def test_two_players_single_pair(self) -> None:
        board = _board(admin=1500, junior=1600)
        assert _optimal_pairing(board, ["admin", "junior"]) == [("admin", "junior")]

    def test_four_players_minimises_total_gap(self) -> None:
        # ratings : A=1500, B=1510, C=1600, D=1610.
        # 3 possible pairings :
        #   (A,B)(C,D) : |10| + |10| = 20
        #   (A,C)(B,D) : |100| + |100| = 200
        #   (A,D)(B,C) : |110| + |90|  = 200
        # Optimal is (A,B)(C,D).
        board = _board(a=1500, b=1510, c=1600, d=1610)
        pairings = _optimal_pairing(board, ["a", "b", "c", "d"])
        assert set(map(frozenset, pairings)) == {frozenset({"a", "b"}), frozenset({"c", "d"})}

    def test_empty_pool(self) -> None:
        assert _optimal_pairing(RatingBoard(), []) == []

    def test_deterministic_tiebreak(self) -> None:
        """Equal gaps → canonical (sorted) pair list wins the tie-break."""
        # All ratings equal → every pairing has zero gap. The canonical
        # tie-break picks the pairing whose sorted pair list is
        # lexicographically smallest.
        board = _board(a=1500, b=1500, c=1500, d=1500)
        pairings = _optimal_pairing(board, ["a", "b", "c", "d"])
        # Lex min of all equal-cost pairings = ((a,b),(c,d)).
        assert pairings == [("a", "b"), ("c", "d")]


class TestChooseBye:
    def test_fewest_games_wins_bye(self) -> None:
        board = _board_with_games(
            admin=(1500, 10),
            leader=(1500, 10),
            junior=(1500, 3),  # fewest → bye
        )
        byed, rest = _choose_bye(board, ["admin", "leader", "junior"])
        assert byed == "junior"
        assert set(rest) == {"admin", "leader"}

    def test_tie_break_alphabetical(self) -> None:
        board = _board_with_games(
            admin=(1500, 5),
            junior=(1500, 5),
            leader=(1500, 5),
        )
        byed, _ = _choose_bye(board, ["admin", "junior", "leader"])
        assert byed == "admin"  # alphabetical among games==5


class TestRematchPenalty:
    """CD review 2026-10-06 PR #297 cycle 3, Stéphane arbitrage (b)."""

    def _rated(self, *, game_id, white, black, round=1, date="2026.10.06", mtime=1.0):
        return RatedGame(
            game_id=game_id,
            white=white,
            black=black,
            date=date,
            mtime=mtime,
            white_score=0.5,
            round=round,
        )

    def test_last_round_pairs_uses_canonical_key_not_mtime(self) -> None:
        """CD cycle 3 : mtime jittered doesn't change the "last opponent" set.

        The old 24h-mtime algorithm would include the oldest game if
        its mtime happened to be bumped recently (e.g. the PGN was
        re-written on a late move or a stamping pass). The canonical
        key ``(round, date, game_id)`` is immune.
        """
        history = [
            # Round 1 game at mtime 1_000_000 (large value simulating a
            # recent file rewrite — but logically it's the FIRST game).
            self._rated(
                game_id="r1-admin-junior",
                white="admin",
                black="junior",
                round=1,
                mtime=1_000_000.0,
            ),
            # Round 2 game at mtime 1.0 (older touch but it's logically
            # the second game).
            self._rated(
                game_id="r2-admin-leader",
                white="admin",
                black="leader",
                round=2,
                mtime=1.0,
            ),
            # Round 2 game for the other pair, same round.
            self._rated(
                game_id="r2-junior-cd",
                white="junior",
                black="claude-desktop",
                round=2,
                mtime=1.0,
            ),
        ]
        pairs = _last_round_pairs(history)
        # The round-2 pairs are the "last opponents" for every player.
        # The round-1 (admin, junior) pair is NOT in the result because
        # admin's last opponent is now leader and junior's is cd.
        assert pairs == frozenset(
            {
                frozenset({"admin", "leader"}),
                frozenset({"junior", "claude-desktop"}),
            }
        )

    def test_last_round_pairs_empty_history(self) -> None:
        assert _last_round_pairs([]) == frozenset()

    def test_last_round_pairs_mcp_round1_overrides_cli_round3_next_day(
        self, tmp_path: Path
    ) -> None:
        """CD cycle 4 : date dominates round in chronology_key.

        Scenario : CLI chess-matchmaking creates a round 3 on day J,
        then a user calls chess_create_game MCP with default
        ``round="1"`` on day J+1. The MCP game must be considered
        the fresher one (same players shouldn't be able to replay
        immediately via the manual MCP path).
        """
        from palamede_chessboard.chess_elo import collect_rated_games

        # Day J : CLI R3 pair = (admin, junior).
        _write_pgn(
            tmp_path,
            game_id="cli-r3-admin-junior",
            white="admin",
            black="junior",
            result="1-0",
            date="2026.10.06",
            round_="3",
        )
        # Day J+1 : MCP default Round "1", pair = (leader, cd).
        _write_pgn(
            tmp_path,
            game_id="mcp-manual-leader-cd",
            white="leader",
            black="claude-desktop",
            result="1/2-1/2",
            date="2026.10.07",
            round_="1",
        )

        history = collect_rated_games(games_dir=tmp_path)
        pairs = _last_round_pairs(history)
        # leader and cd's last opponents are each other (fresher by date).
        # admin and junior's last opponent is still each other (R3 on J).
        assert pairs == frozenset(
            {
                frozenset({"admin", "junior"}),
                frozenset({"leader", "claude-desktop"}),
            }
        )
        # The MCP game must dominate for leader and cd on the next
        # schedule — swap keys and verify the mcp-manual pair is in.

        mcp_game = next(g for g in history if g.game_id == "mcp-manual-leader-cd")
        cli_game = next(g for g in history if g.game_id == "cli-r3-admin-junior")
        assert mcp_game.chronology_key > cli_game.chronology_key

    def test_last_round_pairs_immune_to_future_utime_on_old_pgn(self, tmp_path: Path) -> None:
        """CD cycle 3 explicit : an old PGN with os.utime far future doesn't move the needle."""
        import os
        import time

        from palamede_chessboard.chess_elo import collect_rated_games

        # Round 1 : (admin, junior).
        _write_pgn(
            tmp_path,
            game_id="r1-admin-junior",
            white="admin",
            black="junior",
            result="1-0",
            round_="1",
        )
        # Round 2 : (admin, leader) + (junior, cd).
        _write_pgn(
            tmp_path,
            game_id="r2-admin-leader",
            white="admin",
            black="leader",
            result="1/2-1/2",
            round_="2",
        )
        _write_pgn(
            tmp_path,
            game_id="r2-junior-cd",
            white="junior",
            black="claude-desktop",
            result="1/2-1/2",
            round_="2",
        )

        # Attack vector : bump the round-1 PGN mtime WAY into the future.
        future = time.time() + 10_000.0
        os.utime(tmp_path / "r1-admin-junior.pgn", (future, future))

        history = collect_rated_games(games_dir=tmp_path)
        pairs = _last_round_pairs(history)
        # Despite the future mtime, round 1 pair is NOT the "last opponent"
        # for anyone — round-2 wins on the canonical key.
        assert pairs == frozenset(
            {
                frozenset({"admin", "leader"}),
                frozenset({"junior", "claude-desktop"}),
            }
        )

    def test_optimal_pairing_demotes_recent_rematch(self) -> None:
        """A recent pair pays REMATCH_PENALTY and loses to any alt."""
        # Natural Elo-optimal pairing is (admin+junior)(leader+cd), gap 76.
        # Mark that exact pair as "just played" → penalty 2000 overwhelms,
        # alternative (admin+leader)(junior+cd) with gap 122 wins.
        board = _board(
            admin=1496,
            junior=1443,
            leader=1519,
            **{"claude-desktop": 1542},
        )
        rematch = frozenset(
            {
                frozenset({"admin", "junior"}),
                frozenset({"leader", "claude-desktop"}),
            }
        )
        pairings = _optimal_pairing(
            board,
            ["admin", "junior", "leader", "claude-desktop"],
            rematch_pairs=rematch,
        )
        pair_sets = {frozenset(p) for p in pairings}
        # Both demoted pairs must be absent from the result.
        assert frozenset({"admin", "junior"}) not in pair_sets
        assert frozenset({"leader", "claude-desktop"}) not in pair_sets

    def test_rematch_penalty_cp_dominates_elo_gap(self) -> None:
        """REMATCH_PENALTY_CP must be larger than any realistic Elo gap."""
        # 1000 cp > 400 cp (CD 2026-10-05 noted 100 cp as realistic max
        # in the 4-agent pool). Safety margin confirmed by this test so
        # a future adjustment of the constant stays explicit.
        assert REMATCH_PENALTY_CP > 400

    def test_schedule_round_uses_rematch_penalty(self) -> None:
        """End-to-end : schedule_round plugs the rematch penalty itself."""
        board = _board(
            admin=1496,
            junior=1443,
            leader=1519,
            **{"claude-desktop": 1542},
        )
        history = [
            self._rated(
                game_id="r1-match-a",
                white="admin",
                black="junior",
                round=1,
            ),
            self._rated(
                game_id="r1-match-b",
                white="leader",
                black="claude-desktop",
                round=1,
            ),
        ]
        schedule = schedule_round(board, games_in_progress=[], history=history)
        pair_sets = {frozenset({m.white, m.black}) for m in schedule.matches}
        assert frozenset({"admin", "junior"}) not in pair_sets
        assert frozenset({"leader", "claude-desktop"}) not in pair_sets

    def test_consecutive_rounds_disjoint(self, tmp_path: Path) -> None:
        """CD cycle 3 e2e : rounds consécutifs toujours disjoints.

        Garantie forte du pari (b) : la pénalité "last opponent"
        empêche la revanche immédiate R→R+1. Pour rotation complète
        sur 3 rondes (6 paires du round-robin), il faudrait des Elos
        suffisamment différents pour briser les ties lex — pas
        garanti algorithmiquement.
        """
        from palamede_chessboard.chess_matchmaking import schedule_round_from_disk

        def _play_round(round_num: int) -> set[frozenset[str]]:
            schedule = schedule_round_from_disk(games_dir=tmp_path)
            round_pairs: set[frozenset[str]] = set()
            for m in schedule.matches:
                gid = f"r{round_num}-{m.white}-vs-{m.black}"
                _write_pgn(
                    tmp_path,
                    game_id=gid,
                    white=m.white,
                    black=m.black,
                    result="1/2-1/2",
                    round_=str(round_num),
                )
                round_pairs.add(frozenset({m.white, m.black}))
            return round_pairs

        r1 = _play_round(1)
        r2 = _play_round(2)
        r3 = _play_round(3)
        assert len(r1) == 2 and len(r2) == 2 and len(r3) == 2
        # The core guarantee : no immediate rematch.
        assert r1 & r2 == set(), f"R1 and R2 share pairs : {r1 & r2}"
        assert r2 & r3 == set(), f"R2 and R3 share pairs : {r2 & r3}"
        # R1 ↔ R3 non-disjoint is acceptable (perfect ties with lex
        # tie-break can resettle). Verified by observation on the
        # perfectly-tied board : R1=R3=(admin,cd)(junior,leader),
        # R2=(admin,junior)(leader,cd). The pair-set-of-sets is
        # still 2 distinct pairings over 3 rounds.
