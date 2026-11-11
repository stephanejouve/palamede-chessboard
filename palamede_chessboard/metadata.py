"""Identity and headers carried alongside a game's PGN.

Split out of :mod:`palamede_chessboard.chessboard_state` in BT-001
commit 2 to keep the state module under 500 lines. All public
symbols are re-exported from ``chessboard_state`` for backward
compat.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime

from palamede_chessboard.errors import ChessboardError

#: Characters a header value may not hold (#128) : ``"`` and ``\`` would need
#: the PGN escapes, which python-chess neither writes nor reads back, and the
#: braces would close the ``{termination}`` comment of the movetext. Control
#: characters, the line breaks among them, are refused too.
FORBIDDEN_IN_HEADER = frozenset('"\\{}')

#: Default time control for new tournament games (chantier ELO volet 3a,
#: Stéphane arbitrage 2026-10-05) : « classique » 30 min + 30 sec Fischer,
#: adaptée à la cadence par correspondance des 4 agents. Les anciennes
#: parties sans header TimeControl restent ``"unrated"`` grâce au
#: ``.get("TimeControl", "unrated")`` explicite dans le parser PGN
#: (``GameState.load``).
DEFAULT_TIME_CONTROL_FORMAT = "30+30"

#: ``M+S`` : the internal time-control format, minutes + seconds.
CLOCK_FORMAT_RE = re.compile(r"^\s*(\d+)\s*\+\s*(\d+)\s*$")

#: The PGN standard writes ``TimeControl`` in seconds (``900+30``), while
#: games written before #151 hold minutes (``15+30``). Both read alike, and
#: ``60+0`` is one minute in one form, an hour in the other : the writer
#: puts this header beside a ``TimeControl`` in seconds, and only a
#: ``TimeControl`` that carries it is read as seconds (#151).
TIME_CONTROL_UNIT_TAG = "TimeControlUnit"


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


def parse_time_control(tc: TimeControl) -> tuple[int, int] | None:
    """Return ``(start_seconds, increment_seconds)`` or ``None`` if disabled.

    Priority : explicit ``start_seconds`` / ``increment_seconds`` fields,
    then ``format`` string ``"M+S"`` (minutes + seconds). ``unrated``
    disables the clock.
    """
    if tc.format == "unrated" and tc.start_seconds is None:
        return None
    if tc.start_seconds is not None:
        return (
            int(tc.start_seconds),
            int(tc.increment_seconds or 0),
        )
    match = CLOCK_FORMAT_RE.match(tc.format)
    if not match:
        return None
    minutes, increment = match.groups()
    return (int(minutes) * 60, int(increment))


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
    #: Relay teams (#280) : the registry ids that hold each side, written
    #: ``WhiteTeam`` / ``BlackTeam``. ``white`` / ``black`` stay the team's
    #: shown name. Set at creation only ; empty for a game between two players.
    white_team: list[str] = field(default_factory=list)
    black_team: list[str] = field(default_factory=list)
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
    #: The PGN ``Termination`` header (#151) : ``"normal"`` for a game ended
    #: on the board, by resignation or agreement, ``"time forfeit"`` when a
    #: flag fell. ``None`` while the game is on, and for the games ended
    #: before #151 without a note.
    termination: str | None = None
    #: Whether this game counts for Elo rating. Server games default to
    #: ``True`` ; imports (lichess, GM databases, …) carry no ``Rated``
    #: header and are filtered out by :func:`palamede_chessboard.chess_elo.pgn_scan._parse_rated_game`.
    #: Stéphane arbitrage 2026-10-03 : the Elo perimeter is driven by this
    #: dedicated marker, not by the ``Site`` header.
    rated: bool = True
    #: Kind of session (#323, #328) : ``"cours"`` for a lesson, written ``Mode``.
    #: A description only : the rules read the id's prefix. ``None`` for the
    #: games created before, which carry no ``Mode``.
    mode: str | None = None

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
        if self.white_team:
            headers["WhiteTeam"] = ",".join(self.white_team)
        if self.black_team:
            headers["BlackTeam"] = ",".join(self.black_team)
        clock = parse_time_control(self.time_control)
        if clock is not None:
            # The PGN standard counts seconds (#151) ; the unit tag tells
            # them from the minutes of the games written before.
            headers["TimeControl"] = f"{clock[0]}+{clock[1]}"
            headers[TIME_CONTROL_UNIT_TAG] = "seconds"
        elif self.time_control.format == "unrated":
            headers["TimeControl"] = "-"
        else:
            # A label no clock reads (``40/7200:0+10`` of an import) : kept.
            headers["TimeControl"] = self.time_control.format
        if self.draw_offer:
            headers["DrawOffer"] = f"{self.draw_offer['by']}:{self.draw_offer['ply']}"
        if self.termination:
            headers["Termination"] = self.termination
        if self.rated:
            # Elo perimeter marker (Stéphane arbitrage 2026-10-03). Only
            # written when True so an imported PGN that happens to pass
            # through our writer path stays excluded without a dedicated
            # ``rated=False`` opt-out call.
            headers["Rated"] = "true"
        if self.mode:
            headers["Mode"] = self.mode
        return headers

    def pgn_headers_to_write(self) -> dict[str, str]:
        """:meth:`to_pgn_headers`, refused if a value would break out of its ``[Tag "value"]`` (#128).

        Raises :class:`ChessboardError` before anything is written, so a
        forged value never reaches the PGN, and a game read back keeps the
        exact headers it was saved with.
        """
        headers = self.to_pgn_headers()
        for tag, value in headers.items():
            for char in value:
                if char in FORBIDDEN_IN_HEADER or unicodedata.category(char) == "Cc":
                    raise ChessboardError(f"PGN header {tag}: character {char!r} is not allowed")
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
