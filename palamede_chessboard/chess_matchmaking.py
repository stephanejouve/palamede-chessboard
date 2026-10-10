"""Automatic matchmaking for the four-agent chess tournament.

Chantier ELO volet 3b (post PR #296) : given the current Elo board,
the games already in progress and the historical color balance per
pair, this module decides which agents play whom in the next round
and who gets the white pieces.

Scope (Stéphane arbitrage 2026-10-05) :

* The player pool is the four canonical agents of
  :data:`palamede_chessboard.chess_elo.CANONICAL_IDENTITIES` : ``admin``,
  ``leader``, ``junior``, ``claude-desktop``. An agent that already
  has a game in progress is excluded from the round (one game at a
  time per agent).
* Pairings minimise the sum of absolute Elo gaps across the round —
  brute-force over the three possible perfect matchings when N=4,
  trivial when N<=2.
* Colors alternate based on the per-pair history read from the full
  ``collect_rated_games`` list : the agent who has played **black**
  the most times against the opponent gets white. Ties break on the
  alphabetical order of the player names so the result is
  deterministic.
* Odd pool size → one agent sits out ("bye"). We bye the agent with
  the fewest rated games played (newest in the system) so veterans
  keep playing while a provisional-K newcomer waits one round.

The module is a pure function on the inputs ; actual game creation
(PGN file + metadata + clock init) lives in the CLI / MCP wrapper.

BT-006 V2 (palamede-chessboard#11) : extracted from
``outillages.chess_matchmaking`` as part of the palamede-chessboard
migration. The outillages dependency on ``chessboard_state.list_games``
is replaced here by a local minimal :func:`_scan_active_games` that
reads only the PGN headers needed for the ``games_in_progress`` input
(``White``/``Black``/``Result``). The full ``list_games`` with
move-level introspection stays in outillages until the chessboard
state extraction (future V2-bis).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from pathlib import Path

import chess.pgn

from palamede_chessboard.chess_elo import (
    CANONICAL_IDENTITIES,
    GAMES_DIR,
    INITIAL_RATING,
    RatingBoard,
    cache_is_stale,
    collect_rated_games,
    load_cached_ratings,
    normalize_identity,
    rebuild_and_persist_cache,
)

#: Canonical player pool, sorted for deterministic iteration.
PLAYER_POOL: tuple[str, ...] = tuple(sorted(CANONICAL_IDENTITIES))

#: Elo gap added to a pairing cost when the pair reused is a "last
#: opponent" for one of the players (CD review 2026-10-06 PR #297
#: cycle 3, Stéphane arbitrage (b)). 1000 cp is far above any
#: realistic Elo gap in the four-agent pool (seen max ~100 cp today),
#: so a last-opponent pair is always demoted unless no alternative is
#: possible (N=2 : 1 only pairing).
REMATCH_PENALTY_CP: int = 1000


@dataclass(frozen=True)
class ScheduledMatch:
    """One (white, black) pairing with its diagnostic Elo gap."""

    white: str
    black: str
    white_rating: int
    black_rating: int

    @property
    def elo_gap(self) -> int:
        return abs(self.white_rating - self.black_rating)


@dataclass(frozen=True)
class RoundSchedule:
    """Full output of :func:`schedule_round` — matches + bye + excluded."""

    matches: tuple[ScheduledMatch, ...]
    bye: str | None
    excluded_busy: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "matches": [
                {
                    "white": m.white,
                    "black": m.black,
                    "white_rating": m.white_rating,
                    "black_rating": m.black_rating,
                    "elo_gap": m.elo_gap,
                }
                for m in self.matches
            ],
            "bye": self.bye,
            "excluded_busy": list(self.excluded_busy),
        }


def _scan_active_games(games_dir: Path) -> list[dict]:
    """Minimal replacement for ``outillages.chessboard_state.list_games``.

    Scans ``*.pgn`` files in ``games_dir`` and returns a list of dicts
    with the ``white``/``black`` fields for games whose PGN ``Result``
    header is still ``"*"`` (game in progress). This is the only input
    :func:`schedule_round` needs from the games-in-progress list — the
    richer :func:`outillages.chessboard_state.list_games` (ply count,
    turn, is_game_over, mtime, …) is intentionally out of scope for
    the matchmaker, which only cares *who is busy*.

    Malformed PGN files are silently skipped — one broken file does
    not blind the caller to the rest of its games. Non-existent dir
    returns an empty list.

    TODO (palamede-chessboard#14) : unify with the future
    ``palamede_chessboard.chessboard_state.list_games`` once that
    module is extracted (BT-006 V2-bis / BT-007).
    """
    if not games_dir.is_dir():
        return []
    entries: list[dict] = []
    for pgn_path in sorted(games_dir.glob("*.pgn")):
        try:
            with pgn_path.open("r", encoding="utf-8") as f:
                game = chess.pgn.read_game(f)
        except OSError:
            continue
        if game is None:
            continue
        headers = game.headers
        result = headers.get("Result", "").strip()
        if result != "*":
            continue
        entries.append(
            {
                "white": headers.get("White", ""),
                "black": headers.get("Black", ""),
            }
        )
    return entries


def _available_players(games_in_progress: list[dict]) -> tuple[list[str], list[str]]:
    """Split :data:`PLAYER_POOL` into (available, excluded_busy).

    An agent is "busy" if any entry in ``games_in_progress`` has them
    on either side, normalised via :func:`normalize_identity` (so a
    PGN that spelled "DevLeader" still maps to the canonical "leader").
    """
    busy: set[str] = set()
    for g in games_in_progress:
        for side in ("white", "black"):
            name = normalize_identity(g.get(side, ""))
            if name in CANONICAL_IDENTITIES:
                busy.add(name)
    available = [p for p in PLAYER_POOL if p not in busy]
    excluded = sorted(busy & set(PLAYER_POOL))
    return available, excluded


def _rating_of(board: RatingBoard, player: str) -> int:
    """Return the current Elo of ``player``, or ``INITIAL_RATING`` if new."""
    entry = board.players.get(player)
    return entry.rating if entry is not None else INITIAL_RATING


def _games_played(board: RatingBoard, player: str) -> int:
    entry = board.players.get(player)
    return entry.games if entry is not None else 0


def _choose_bye(board: RatingBoard, players: list[str]) -> tuple[str, list[str]]:
    """Pick the ``bye`` agent (fewest rated games) and return the rest.

    Tie-break on alphabetical order so the choice is deterministic
    across runs. ``bye`` sits out ; the remaining agents must be of
    even cardinality for :func:`_optimal_pairing` to work.
    """
    assert len(players) % 2 == 1
    byed = min(players, key=lambda p: (_games_played(board, p), p))
    rest = [p for p in players if p != byed]
    return byed, rest


def _last_round_pairs(history: list) -> frozenset[frozenset[str]]:
    """Return the set of (player, last_opponent) pairs across all players.

    CD review 2026-10-06 PR #297 cycle 3 (fix of cycle 2 regression) :
    the mtime-based 24h window was inert in production — every PGN
    write (each move via ``GameState.save``) bumps mtime, so an old
    game can look recent while a truly recent game can look older
    depending on move ordering. Fix : use the canonical
    :attr:`RatedGame.chronology_key` ``(date, round, game_id)`` so
    the ordering only reflects **when the game was played**, not when
    the file was last touched. Cycle 4 : key reordered to put
    ``date`` first (bugfix for the mix MCP ``round=1`` default vs
    CLI ``round=max+1``).

    For every player who appears in ``history``, we find their most
    recent game by ``chronology_key`` and penalise the pair they
    played in it. **Guarantee** : no immediate rematch (round N vs
    round N+1 are disjoint pair-sets whenever an alternative exists).
    Full round-robin coverage (all 6 pairs of 4 agents in 3 rounds)
    is NOT guaranteed when ratings are perfectly tied — the
    deterministic lex tie-break can resettle the round-1 pattern at
    round 3. Live Elo gaps break those ties naturally.
    """
    if not history:
        return frozenset()
    # Oldest first → the last iteration per player overwrites with
    # the most recent game, giving us the "last opponent" lookup.
    sorted_h = sorted(history, key=lambda g: g.chronology_key)
    last_pair_by_player: dict[str, frozenset[str]] = {}
    for g in sorted_h:
        pair = frozenset({normalize_identity(g.white), normalize_identity(g.black)})
        for p in pair:
            last_pair_by_player[p] = pair
    return frozenset(last_pair_by_player.values())


def _optimal_pairing(
    board: RatingBoard,
    players: list[str],
    *,
    rematch_pairs: frozenset[frozenset[str]] = frozenset(),
) -> list[tuple[str, str]]:
    """Return pairings minimising total absolute Elo gap (plus rematch penalty).

    For N=0 or N=2 the answer is trivial. For larger N we enumerate
    perfect matchings and pick the one minimising ``sum|elo_a - elo_b|
    + REMATCH_PENALTY_CP * (count of pairs recently played)``.
    Deterministic tie-break on the lexicographic order of the pairing.

    ``rematch_pairs`` is the set of pairs from :func:`_last_round_pairs`
    — any pairing that reuses one of them pays ``REMATCH_PENALTY_CP``
    per reused pair. For N=4 with one round of history, the two paths
    that avoid both prior pairs win over the identity re-match.
    """
    assert len(players) % 2 == 0
    if not players:
        return []
    if len(players) == 2:
        a, b = sorted(players)
        return [(a, b)]
    best: list[tuple[str, str]] | None = None
    best_cost: tuple[int, tuple] | None = None
    for perm in permutations(players):
        # Group consecutive pairs : (p0,p1), (p2,p3), ...
        pairs = tuple(tuple(sorted((perm[i], perm[i + 1]))) for i in range(0, len(perm), 2))
        # Canonical form for tie-break : sort the pair list itself.
        canonical = tuple(sorted(pairs))
        if canonical != pairs:
            continue
        total_gap = sum(abs(_rating_of(board, a) - _rating_of(board, b)) for a, b in pairs)
        rematch_count = sum(1 for a, b in pairs if frozenset({a, b}) in rematch_pairs)
        total = total_gap + rematch_count * REMATCH_PENALTY_CP
        key = (total, canonical)
        if best_cost is None or key < best_cost:
            best_cost = key
            best = list(pairs)
    assert best is not None  # unreachable with N>=2
    return best


def _white_count_for_pair(history: list, a: str, b: str) -> tuple[int, int]:
    """Return ``(a_as_white, b_as_white)`` across their rated history.

    Reads :class:`RatedGame` entries from
    :func:`palamede_chessboard.chess_elo.collect_rated_games`. Game
    pairs whose sides don't match ``{a, b}`` are skipped.
    """
    pair = {a, b}
    a_white = 0
    b_white = 0
    for g in history:
        gw = normalize_identity(g.white)
        gb = normalize_identity(g.black)
        if {gw, gb} != pair:
            continue
        if gw == a:
            a_white += 1
        elif gw == b:
            b_white += 1
    return a_white, b_white


def _global_white_count(history: list, player: str) -> int:
    """Return how many times ``player`` played white across all rated games."""
    return sum(1 for g in history if normalize_identity(g.white) == player)


def _assign_colors(history: list, player_a: str, player_b: str) -> tuple[str, str]:
    """Return ``(white, black)`` so the color balance is nudged towards parity.

    Priority order (CD review 2026-10-06 PR #297 mineur) :

    1. Per-pair : the agent who has played **more black** against this
       specific opponent gets white in the new game.
    2. Tie-break global : the agent who has played **fewer whites in
       total** gets white (nudges each agent's overall white/black
       distribution towards 50/50 across the whole tournament).
    3. Final tie-break alphabetical for strict determinism.
    """
    a_white, b_white = _white_count_for_pair(history, player_a, player_b)
    if a_white < b_white:
        return player_a, player_b
    if b_white < a_white:
        return player_b, player_a
    # Pair-level tie → global tally.
    a_global = _global_white_count(history, player_a)
    b_global = _global_white_count(history, player_b)
    if a_global < b_global:
        return player_a, player_b
    if b_global < a_global:
        return player_b, player_a
    # Still tied → alphabetical for determinism.
    first, second = sorted((player_a, player_b))
    return first, second


def schedule_round(
    board: RatingBoard,
    games_in_progress: list[dict],
    history: list,
) -> RoundSchedule:
    """Compute the next round of matches for the four-agent tournament.

    Inputs :

    * ``board`` : current ratings (output of
      :func:`load_cached_ratings` or :func:`rebuild_and_persist_cache`).
    * ``games_in_progress`` : list of dicts with ``white``/``black``
      keys (output of :func:`_scan_active_games` or the equivalent
      upstream ``list_games`` filtered to in-progress). Agents on
      either side of any entry are excluded from the round.
    * ``history`` : output of
      :func:`palamede_chessboard.chess_elo.collect_rated_games`.
      Drives the color-alternation decision.

    Output : one :class:`RoundSchedule` with the matches, the bye (if
    any) and the list of agents excluded for being busy.
    """
    available, excluded = _available_players(games_in_progress)
    bye: str | None = None
    if len(available) % 2 == 1:
        bye, available = _choose_bye(board, available)
    rematch_pairs = _last_round_pairs(history)
    pairings = _optimal_pairing(board, available, rematch_pairs=rematch_pairs)
    matches: list[ScheduledMatch] = []
    for a, b in pairings:
        white, black = _assign_colors(history, a, b)
        matches.append(
            ScheduledMatch(
                white=white,
                black=black,
                white_rating=_rating_of(board, white),
                black_rating=_rating_of(board, black),
            )
        )
    return RoundSchedule(
        matches=tuple(matches),
        bye=bye,
        excluded_busy=tuple(excluded),
    )


def schedule_round_from_disk(games_dir: Path | None = None) -> RoundSchedule:
    """Thin wrapper that loads everything from the games directory.

    Convenience for the CLI / MCP tool — no extra arguments beyond
    the games directory. CD review 2026-10-06 PR #297 bloquant 1 :
    use :func:`cache_is_stale` so a cache that exists but was built
    before the latest finished game still triggers a rebuild. The
    previous version only rebuilt when the cache was absent, which
    produced pairings on a stale Elo.
    """
    gdir = games_dir if games_dir is not None else GAMES_DIR
    if cache_is_stale(games_dir=gdir):
        board, _ = rebuild_and_persist_cache(games_dir=gdir)
    else:
        board = load_cached_ratings(games_dir=gdir)
    games_in_progress = _scan_active_games(gdir)
    history = collect_rated_games(games_dir=gdir)
    return schedule_round(board, games_in_progress, history)
