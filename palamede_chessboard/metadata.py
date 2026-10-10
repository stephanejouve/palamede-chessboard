"""Identity and headers carried alongside a game's PGN.

Split out of :mod:`palamede_chessboard.chessboard_state` in BT-001
commit 2 to keep the state module under 500 lines. All public
symbols are re-exported from ``chessboard_state`` for backward
compat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from palamede_chessboard.errors import ChessboardError

#: Default time control for new tournament games (chantier ELO volet 3a,
#: Stéphane arbitrage 2026-10-05) : « classique » 30 min + 30 sec Fischer,
#: adaptée à la cadence par correspondance des 4 agents. Les anciennes
#: parties sans header TimeControl restent ``"unrated"`` grâce au
#: ``.get("TimeControl", "unrated")`` explicite dans le parser PGN
#: (``GameState.load``).
DEFAULT_TIME_CONTROL_FORMAT = "30+30"


@dataclass
class TimeControl:
    """Optional time-control envelope attached to a game.

    ``format`` is a free-form label (``"5+3"``, ``"15+10"``,
    ``"30+30"``, ``"unrated"``) ; the clock itself lives in
    :mod:`palamede_chessboard.chessboard_clock` and is wired by the MCP server
    on ``chess_create_game`` (``ClockState.init_from_time_control``).

    Default = :data:`DEFAULT_TIME_CONTROL_FORMAT` so every new
    tournament game ships with a clock unless the caller explicitly
    overrides to ``"unrated"``. Legacy PGN loading still falls back
    to ``"unrated"`` for backward compat.
    """

    format: str = DEFAULT_TIME_CONTROL_FORMAT
    start_seconds: int | None = None
    increment_seconds: int | None = None


@dataclass
class GameMetadata:
    """Identity + headers carried by a game alongside its PGN."""

    game_id: str
    event: str = ""
    site: str = ""
    date: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y.%m.%d"))
    round: str = "1"
    white: str = ""
    black: str = ""
    arbiter: str | None = None
    spectators: list[str] = field(default_factory=list)
    time_control: TimeControl = field(default_factory=TimeControl)
    result: str = "*"
    annotator: str | None = None
    opening: str | None = None
    eco: str | None = None
    #: Pending draw offer awaiting agree/decline. ``{"by": "white"|"black", "ply": int}``.
    #: Cleared on ``agree_draw`` (result → 1/2-1/2), ``decline_draw``, or resign.
    draw_offer: dict | None = None
    #: Termination reason label added when the game ends via a non-checkmate
    #: path (resign, draw agreed). E.g. ``"white resigns"``, ``"draw agreed"``.
    #: Rendered as a PGN ``{...}`` comment before the result marker.
    termination_note: str | None = None
    #: Whether this game counts for Elo rating. Server games default to
    #: ``True`` ; imports (lichess, GM databases, …) carry no ``Rated``
    #: header and are filtered out by :func:`palamede_chessboard.chess_elo.pgn_scan._parse_rated_game`.
    #: Stéphane arbitrage 2026-10-03 : the Elo perimeter is driven by this
    #: dedicated marker, not by the ``Site`` header.
    rated: bool = True

    def to_pgn_headers(self) -> dict[str, str]:
        """Return the subset of fields that map onto PGN ``[Tag "value"]`` headers."""
        headers: dict[str, str] = {
            "Event": self.event,
            "Site": self.site,
            "Date": self.date,
            "Round": self.round,
            "White": self.white,
            "Black": self.black,
            "Result": self.result,
        }
        if self.annotator:
            headers["Annotator"] = self.annotator
        if self.opening:
            headers["Opening"] = self.opening
        if self.eco:
            headers["ECO"] = self.eco
        if self.arbiter:
            headers["Arbiter"] = self.arbiter
        if self.spectators:
            headers["Spectators"] = ",".join(self.spectators)
        if self.time_control.format != "unrated":
            headers["TimeControl"] = self.time_control.format
        if self.draw_offer:
            headers["DrawOffer"] = f"{self.draw_offer['by']}:{self.draw_offer['ply']}"
        if self.termination_note:
            headers["Termination"] = self.termination_note
        if self.rated:
            # Elo perimeter marker (Stéphane arbitrage 2026-10-03). Only
            # written when True so an imported PGN that happens to pass
            # through our writer path stays excluded without a dedicated
            # ``rated=False`` opt-out call.
            headers["Rated"] = "true"
        return headers

    def resolve_side(self, by: str) -> str:
        """Return ``"white"`` or ``"black"`` given either side or player name.

        Raises :class:`ChessboardError` if ``by`` cannot be resolved.
        """
        if by in ("white", "black"):
            return by
        if by and by == self.white:
            return "white"
        if by and by == self.black:
            return "black"
        raise ChessboardError(
            f"Cannot resolve {by!r} to a side "
            f"(expected 'white', 'black', {self.white!r}, or {self.black!r})"
        )
