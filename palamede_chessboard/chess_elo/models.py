"""Dataclasses + rating constants for the Elo system.

Separated from the PGN scanning / cache logic so that pure consumers
(tests, display layer, serialisation) can import the data model
without pulling any filesystem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

INITIAL_RATING = 1500
K_PROVISIONAL = 40
K_ESTABLISHED = 20
PROVISIONAL_GAMES = 30

RATED_HEADER = "Rated"
RATED_HEADER_TRUE = "true"
RATINGS_FILENAME = "ratings.json"


@dataclass(frozen=True)
class PlayerRating:
    """Immutable snapshot of a player's rating state."""

    rating: int
    games: int

    def to_dict(self) -> dict:
        return {"rating": self.rating, "games": self.games}

    @classmethod
    def from_dict(cls, data: dict) -> PlayerRating:
        return cls(rating=int(data["rating"]), games=int(data["games"]))

    @classmethod
    def initial(cls) -> PlayerRating:
        return cls(rating=INITIAL_RATING, games=0)


@dataclass(frozen=True)
class RatedGame:
    """One finished server game, with the fields needed to apply an Elo update."""

    game_id: str
    white: str
    black: str
    date: str  # YYYY.MM.DD (PGN header, may be partial / unknown)
    mtime: float  # file mtime, used as tiebreaker within the same date
    white_score: float  # 1.0 / 0.5 / 0.0
    round: int = 0  # Round header parsed as int ; 0 if "-" / missing / non-int

    @property
    def sort_key(self) -> tuple[str, float, str]:
        """(date, mtime, game_id) — stable ordering across equal timestamps."""
        return (self.date, self.mtime, self.game_id)

    @property
    def chronology_key(self) -> tuple[str, int, str]:
        """``(date, round, game_id)`` — stable ordering INDEPENDENT of mtime.

        CD review cycle 4 (outillages#297) : the key starts with
        ``date`` (not ``round``) because the MCP chessboard server
        (BT-004 à venir) ``chess_create_game`` has a hardcoded
        ``Round "1"`` default, while the CLI
        ``chess-matchmaking create`` numbers rounds
        ``max+1``. A manual MCP game created the day AFTER a CLI
        round 3 would otherwise be sorted as "older" (round 1 < round
        3), so its freshly-played pair could be reused immediately on
        the next ``_last_round_pairs`` lookup. Priority order :

        1. ``date`` — the actual calendar day the game was played.
        2. ``round`` — tie-breaker within the same day, respects the
           CLI's monotonic numbering when it was used.
        3. ``game_id`` — final deterministic tie-breaker.

        **Residual limitation** : if two games on the SAME day carry
        inconsistent ``Round`` headers (one from MCP default ``"1"``
        and one from CLI ``"N>1"``), the CLI game is treated as the
        newer. That matches the common case (user played a quick
        manual game to test, then launched a scheduled round).
        """
        return (self.date, self.round, self.game_id)


@dataclass
class RatingUpdate:
    """What one game changed for its two players (for audit / display)."""

    game_id: str
    white: str
    black: str
    white_before: int
    black_before: int
    white_after: int
    black_after: int
    white_score: float
    k_white: int
    k_black: int

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "white": self.white,
            "black": self.black,
            "white_before": self.white_before,
            "black_before": self.black_before,
            "white_after": self.white_after,
            "black_after": self.black_after,
            "white_delta": self.white_after - self.white_before,
            "black_delta": self.black_after - self.black_before,
            "white_score": self.white_score,
            "k_white": self.k_white,
            "k_black": self.k_black,
        }


@dataclass
class RatingBoard:
    """Final state of a rating rebuild : one entry per player + the audit log.

    CD review cycle 3 (outillages#295) : the board also persists the
    *source state* that produced it :

    * ``source_mtime`` — snapshot of ``max(mtime(*.pgn))`` taken BEFORE
      the rebuild started. Any PGN written during or after the rebuild
      is strictly newer and will trip :func:`cache_is_stale`.
    * ``source_pgns`` — sorted stems of ``*.pgn`` files that were part
      of the rebuild. Catches deletions / renames (set-diff vs the
      current directory listing). Independent of filesystem mtime
      semantics — immune to ``os.utime`` permission issues (arbiter
      user without ownership of ``/Users/Shared/games``).

    These fields default to ``None`` / ``[]`` for backward compat with
    pre-cycle-3 cache files. :func:`cache_is_stale` treats a missing
    ``source_mtime`` as stale by convention so an upgrade triggers a
    rebuild on first access.
    """

    players: dict[str, PlayerRating] = field(default_factory=dict)
    history: list[RatingUpdate] = field(default_factory=list)
    source_mtime: float | None = None
    source_pgns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "players": {name: pr.to_dict() for name, pr in self.players.items()},
            "history": [u.to_dict() for u in self.history],
            "source_mtime": self.source_mtime,
            "source_pgns": list(self.source_pgns),
        }

    @classmethod
    def from_dict(cls, data: dict) -> RatingBoard:
        board = cls()
        for name, raw in data.get("players", {}).items():
            board.players[name] = PlayerRating.from_dict(raw)
        # History is informational — we keep it as raw dicts via a round-trip,
        # but the primary consumer is JSON display, so re-serializing is fine.
        for raw in data.get("history", []):
            board.history.append(
                RatingUpdate(
                    game_id=raw["game_id"],
                    white=raw["white"],
                    black=raw["black"],
                    white_before=raw["white_before"],
                    black_before=raw["black_before"],
                    white_after=raw["white_after"],
                    black_after=raw["black_after"],
                    white_score=raw["white_score"],
                    k_white=raw["k_white"],
                    k_black=raw["k_black"],
                )
            )
        src_mtime = data.get("source_mtime")
        board.source_mtime = float(src_mtime) if src_mtime is not None else None
        board.source_pgns = list(data.get("source_pgns", []))
        return board
