"""Tests for :mod:`palamede_chessboard.chess_matchmaking` (BT-006 V2).

Extracted from ``outillages/tests/test_chess_matchmaking.py`` as part
of the palamede-chessboard migration. Tests that previously depended
on ``outillages.chessboard_state.GameState`` for PGN authoring now
write PGN files **textually** via the local :func:`_write_pgn` helper,
so this suite is fully self-contained inside palamede-chessboard.

Dropped from the outillages suite (out of scope V2) :

* ``TestInitAndSaveClock`` — depends on
  ``outillages.cli_chess_matchmaking`` and
  ``outillages.chessboard_clock``. The CLI + clock side-car extraction
  is a future BT (V4 carnet-de-leçons track).
"""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.chess_elo import (
    PlayerRating,
    RatedGame,
    RatingBoard,
)
from palamede_chessboard.chess_matchmaking import (
    PLAYER_POOL,
    REMATCH_PENALTY_CP,
    _assign_colors,
    _available_players,
    _choose_bye,
    _last_round_pairs,
    _optimal_pairing,
    schedule_round,
)

# --- Textual PGN helpers (self-contained — no outillages dependency) ---


def _write_pgn(
    games_dir: Path,
    *,
    game_id: str,
    white: str,
    black: str,
    result: str,
    date: str = "2026.10.06",
    round_: str = "1",
) -> Path:
    """Dump a minimal rated-server PGN with the given headers.

    Mirrors the ``_create_finished`` / ``_create_live`` helpers in
    :mod:`tests.chess_elo.conftest` but inlined here so the matchmaking
    suite does not take a cross-package dependency on the chess_elo
    test fixtures. The movetext is a legal placeholder (``1. e4 e5``)
    plus the result token — ``collect_rated_games`` and
    ``_scan_active_games`` both only read the headers.
    """
    games_dir.mkdir(parents=True, exist_ok=True)
    headers = {
        "Event": "Test",
        "Site": "Chas, Tiresias",
        "Date": date,
        "Round": round_,
        "White": white,
        "Black": black,
        "Result": result,
        "Rated": "true",
    }
    lines = [f'[{tag} "{value}"]\n' for tag, value in headers.items()]
    text = "".join(lines) + "\n" + f"1. e4 e5 {result}" + "\n"
    path = games_dir / f"{game_id}.pgn"
    path.write_text(text, encoding="utf-8")
    return path


def _board(**players: int) -> RatingBoard:
    """Build a minimal RatingBoard from name→rating kwargs."""
    board = RatingBoard()
    for name, rating in players.items():
        board.players[name] = PlayerRating(rating=rating, games=1)
    return board


def _board_with_games(**players: tuple[int, int]) -> RatingBoard:
    """Same as :func:`_board` but takes ``(rating, games)`` tuples."""
    board = RatingBoard()
    for name, (rating, games) in players.items():
        board.players[name] = PlayerRating(rating=rating, games=games)
    return board


class TestAvailablePlayers:
    def test_all_four_free(self) -> None:
        available, excluded = _available_players([])
        assert set(available) == set(PLAYER_POOL)
        assert excluded == []

    def test_junior_busy(self) -> None:
        available, excluded = _available_players([{"white": "junior", "black": "leader"}])
        assert set(available) == {"admin", "claude-desktop"}
        assert excluded == ["junior", "leader"]

    def test_non_canonical_name_ignored(self) -> None:
        """A PGN that spells a non-canonical name doesn't block anyone."""
        available, _ = _available_players([{"white": "stranger", "black": "visitor"}])
        assert set(available) == set(PLAYER_POOL)

    def test_devleader_alias_blocks_leader(self) -> None:
        """Normalize_identity routes ``devleader`` to ``leader``."""
        available, _ = _available_players([{"white": "devleader", "black": "admin"}])
        assert set(available) == {"junior", "claude-desktop"}


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


class TestAssignColors:
    def test_no_history_alphabetical(self) -> None:
        white, black = _assign_colors([], "junior", "admin")
        assert (white, black) == ("admin", "junior")

    def test_player_who_played_more_black_gets_white(self) -> None:
        # junior was black twice vs admin, admin was black zero times
        # → junior gets white in the new game.
        history = [
            RatedGame(
                game_id="g1",
                white="admin",
                black="junior",
                date="2026.10.01",
                mtime=1.0,
                white_score=1.0,
            ),
            RatedGame(
                game_id="g2",
                white="admin",
                black="junior",
                date="2026.10.02",
                mtime=2.0,
                white_score=0.0,
            ),
        ]
        white, black = _assign_colors(history, "junior", "admin")
        assert (white, black) == ("junior", "admin")

    def test_other_opponents_ignored(self) -> None:
        """Games against a different third player must not pollute the count."""
        history = [
            RatedGame(
                game_id="g1",
                white="junior",
                black="leader",  # <-- not our pair
                date="2026.10.01",
                mtime=1.0,
                white_score=1.0,
            ),
        ]
        white, black = _assign_colors(history, "junior", "admin")
        # No junior-admin history → alphabetical tie-break = admin white.
        assert (white, black) == ("admin", "junior")

    def test_normalize_identity_in_history(self) -> None:
        """A legacy PGN that spells ``DevLeader`` still routes to ``leader``."""
        history = [
            RatedGame(
                game_id="g1",
                white="DevLeader",
                black="admin",
                date="2026.10.01",
                mtime=1.0,
                white_score=1.0,
            ),
        ]
        # Pair history : leader white 1x, admin black 1x. For the next
        # game, the player who has been black MORE in this pair gets
        # white → admin (1 black) > leader (0 black), so admin plays white.
        white, black = _assign_colors(history, "admin", "leader")
        assert (white, black) == ("admin", "leader")


class TestScheduleRound:
    def test_four_players_free_two_matches(self) -> None:
        board = _board(
            admin=1500,
            leader=1510,
            junior=1600,
            **{"claude-desktop": 1610},
        )
        schedule = schedule_round(board, games_in_progress=[], history=[])
        assert len(schedule.matches) == 2
        assert schedule.bye is None
        assert schedule.excluded_busy == ()
        # Optimal pairing : (admin, leader) + (junior, claude-desktop).
        pairs = {frozenset({m.white, m.black}) for m in schedule.matches}
        assert pairs == {
            frozenset({"admin", "leader"}),
            frozenset({"claude-desktop", "junior"}),
        }

    def test_three_free_one_bye(self) -> None:
        board = _board_with_games(
            admin=(1500, 10),
            leader=(1500, 10),
            junior=(1500, 2),  # fewest games → bye
            **{"claude-desktop": (1500, 10)},
        )
        schedule = schedule_round(
            board,
            games_in_progress=[{"white": "claude-desktop", "black": "leader"}],
            history=[],
        )
        # claude-desktop + leader busy → available {admin, junior} → no bye.
        assert schedule.bye is None
        assert len(schedule.matches) == 1
        assert set(schedule.excluded_busy) == {"claude-desktop", "leader"}

    def test_odd_count_byes_lowest_games(self) -> None:
        """3 free players, lowest-games sits out."""
        board = _board_with_games(
            admin=(1500, 20),
            leader=(1500, 20),
            junior=(1500, 2),
            **{"claude-desktop": (1500, 20)},
        )
        schedule = schedule_round(
            board,
            # Only claude-desktop in a game → 3 free (admin, leader, junior).
            games_in_progress=[
                {"white": "claude-desktop", "black": "stranger"},  # non-canonical opp
            ],
            history=[],
        )
        # Of (admin, leader, junior) the one with fewest games is junior.
        assert schedule.bye == "junior"
        assert len(schedule.matches) == 1
        assert set(schedule.excluded_busy) == {"claude-desktop"}

    def test_schedule_to_dict_structure(self) -> None:
        board = _board(admin=1500, leader=1600)
        schedule = schedule_round(
            board,
            games_in_progress=[
                {"white": "junior", "black": "claude-desktop"},
            ],
            history=[],
        )
        data = schedule.to_dict()
        assert "matches" in data and "bye" in data and "excluded_busy" in data
        m = data["matches"][0]
        assert set(m.keys()) == {"white", "black", "white_rating", "black_rating", "elo_gap"}
        assert m["elo_gap"] == 100

    def test_unknown_player_defaults_to_initial_rating(self) -> None:
        """An agent absent from the board enters at :data:`INITIAL_RATING`."""
        from palamede_chessboard.chess_elo import INITIAL_RATING

        board = _board(admin=1500)  # leader absent
        schedule = schedule_round(
            board,
            games_in_progress=[
                {"white": "junior", "black": "claude-desktop"},
            ],
            history=[],
        )
        assert len(schedule.matches) == 1
        m = schedule.matches[0]
        pair = {m.white, m.black}
        assert pair == {"admin", "leader"}
        # Leader absent from board → rated at INITIAL_RATING.
        if m.white == "leader":
            assert m.white_rating == INITIAL_RATING
        else:
            assert m.black_rating == INITIAL_RATING


class TestScheduleRoundFromDisk:
    """Integration test — read from a real games_dir and compute a round."""

    def test_empty_dir_schedules_two_matches(self, tmp_path: Path) -> None:
        """No PGNs → all four at 1500 → two matches, no bye, no excluded."""
        from palamede_chessboard.chess_matchmaking import schedule_round_from_disk

        schedule = schedule_round_from_disk(games_dir=tmp_path)
        assert len(schedule.matches) == 2
        assert schedule.bye is None
        assert schedule.excluded_busy == ()
        # All equal 1500 → tie-break : sorted canonical pairs.
        pairs_sorted = sorted(tuple(sorted((m.white, m.black))) for m in schedule.matches)
        assert pairs_sorted == [
            ("admin", "claude-desktop"),
            ("junior", "leader"),
        ]


class TestScanActiveGames:
    """BT-006 V2 : local minimal replacement for ``list_games``."""

    def test_active_games_detected(self, tmp_path: Path) -> None:
        """A PGN with ``Result "*"`` is reported, finished ones skipped."""
        from palamede_chessboard.chess_matchmaking import _scan_active_games

        _write_pgn(tmp_path, game_id="live", white="admin", black="junior", result="*")
        _write_pgn(tmp_path, game_id="done", white="leader", black="admin", result="1-0")
        entries = _scan_active_games(tmp_path)
        assert len(entries) == 1
        assert entries[0]["white"] == "admin"
        assert entries[0]["black"] == "junior"

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        """Non-existent dir yields an empty list, not a crash."""
        from palamede_chessboard.chess_matchmaking import _scan_active_games

        assert _scan_active_games(tmp_path / "does-not-exist") == []

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        from palamede_chessboard.chess_matchmaking import _scan_active_games

        assert _scan_active_games(tmp_path) == []


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


class TestStaleCacheRebuild:
    """CD review 2026-10-06 PR #297 bloquant 1."""

    def test_schedule_round_from_disk_rebuilds_stale_cache(self, tmp_path: Path) -> None:
        """A cache that exists but is stale must be rebuilt, not reused."""
        import os
        import time

        from palamede_chessboard.chess_elo import (
            load_cached_ratings,
            rebuild_and_persist_cache,
        )
        from palamede_chessboard.chess_matchmaking import schedule_round_from_disk

        # 1) One game, baseline cache persisted.
        _write_pgn(
            tmp_path,
            game_id="r1-admin-junior",
            white="admin",
            black="junior",
            result="1-0",
            round_="1",
        )
        rebuild_and_persist_cache(games_dir=tmp_path)
        before = load_cached_ratings(games_dir=tmp_path)
        admin_before = before.players["admin"].rating

        # 2) Write a second finished game AND force its mtime strictly
        #    greater than the baseline cache's source_mtime (coarse FS
        #    granularity safety).
        _write_pgn(
            tmp_path,
            game_id="r2-junior-admin",
            white="junior",
            black="admin",
            result="1-0",
            round_="2",
        )
        future = time.time() + 1.0
        os.utime(tmp_path / "r2-junior-admin.pgn", (future, future))

        # 3) schedule_round_from_disk must see the second game ; the
        #    cached board was build from one game only, admin's rating
        #    changes after the second (he lost as black).
        schedule = schedule_round_from_disk(games_dir=tmp_path)
        assert schedule is not None
        after = load_cached_ratings(games_dir=tmp_path)
        assert after.players["admin"].rating != admin_before


class TestGlobalWhiteTiebreak:
    """CD review 2026-10-06 PR #297 mineur : départage global."""

    def _rated(self, *, white, black):
        return RatedGame(
            game_id=f"{white}-vs-{black}",
            white=white,
            black=black,
            date="2026.10.06",
            mtime=1.0,
            white_score=0.5,
        )

    def test_global_tally_breaks_pair_level_tie(self) -> None:
        """When the pair history is tied, pick by global white count."""
        # admin and junior : no history together at all.
        # BUT admin has already played 3 whites in total (vs other opps),
        # junior has played 0 whites in total.
        # → junior deserves black less (and white more) overall → junior
        # gets white in the new game. Admin stays black.
        history = [
            self._rated(white="admin", black="leader"),
            self._rated(white="admin", black="claude-desktop"),
            self._rated(white="admin", black="leader"),
        ]
        white, black = _assign_colors(history, "admin", "junior")
        assert (white, black) == ("junior", "admin")

    def test_global_tally_tied_falls_back_alphabetical(self) -> None:
        """Pair tied AND global tied → alphabetical final tie-break."""
        history: list[RatedGame] = []
        white, black = _assign_colors(history, "junior", "admin")
        assert (white, black) == ("admin", "junior")
