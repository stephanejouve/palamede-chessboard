"""Chess game state with PGN persistence and commentary log.

Backs the future ``mcp_chessboard_server`` MCP tools. Wraps
``chess.Board`` with three concerns layered on top:

* **Identity** — a game has ``players`` (white/black), an optional
  ``arbiter``, an optional spectator list, and an optional
  ``time_control`` envelope. Identities are free-form strings (e.g.
  ``"admin"``, ``"leader"``, ``"junior"``, ``"tephra"``).
* **Move validation** — every push goes through
  :py:meth:`chess.Board.parse_san`, so illegal SAN is rejected at the
  MCP boundary rather than left to manual arbitration.
* **Persistence** — a PGN file under :data:`GAMES_DIR` is rewritten on
  every successful move ; a side-car JSONL log accumulates ply-indexed
  commentary entries.

Scope = skeleton (T-mcp-chessboard-server). Round-2 lessons : the five
errata that slipped past the human arbiter on 2026-05-25/26 (20.Qxd3,
42.bxa5, 42...Rxc3, 53...Ka4, 62.Kc1) were all detectable by
``parse_san`` ; the goal of moving validation server-side is to make
that class of mistake impossible by construction.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import chess
import chess.pgn

log = logging.getLogger(__name__)

GAMES_DIR = Path("/Users/Shared/games")
COMMENTARY_DIR_NAME = "commentary"
GAME_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")


class ChessboardError(Exception):
    """Base error raised by chessboard_state."""


class IllegalMoveError(ChessboardError):
    """Raised when a SAN move is rejected by ``chess.Board.parse_san``."""


class GameNotFoundError(ChessboardError):
    """Raised when a referenced ``game_id`` has no PGN on disk."""


class GameAlreadyExistsError(ChessboardError):
    """Raised on ``create`` when the ``game_id`` PGN already exists."""


class InvalidGameStateError(ChessboardError):
    """Raised when an operation is illegal in the current game state.

    Examples: ``resign`` after the game already ended ; ``agree_draw``
    with no pending offer ; ``agree_draw`` on one's own offer.
    """


class StalePlyError(ChessboardError):
    """Raised when ``play_move(expected_ply=…)`` is given but the board
    is no longer at that ply (another player — or the same agent in a
    duplicate invocation — already pushed a move since the caller read
    the state).

    Attributes ``current_ply`` and ``expected_ply`` are exposed so the
    MCP handler can surface them in the structured error payload.
    """

    def __init__(self, current_ply: int, expected_ply: int) -> None:
        super().__init__(f"Stale ply: expected {expected_ply}, board at {current_ply}")
        self.current_ply = current_ply
        self.expected_ply = expected_ply


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of :func:`validate_san_strict`.

    Inlined in BT-001 M1 (palamede extraction) — the standalone
    ``chess_arbiter`` module that owns strict validation + move
    request context is extracted in BT-003 (palamede#8). Keeping the
    rules self-contained in ``chessboard_state`` for M1 avoids a
    cross-module dependency cycle during the bottom-up migration.
    """

    ok: bool
    reason: str = ""


def validate_san_strict(board: chess.Board, san: str) -> ValidationResult:
    """Return ``ok=True`` iff ``san`` parses on ``board`` AND its suffix
    symbols match the actual move state.

    Rejected on :

    - ``chess.IllegalMoveError`` / ``chess.InvalidMoveError`` /
      ``chess.AmbiguousMoveError`` from ``parse_san``.
    - ``'x' in san`` but ``board.is_capture(move) is False`` (ghost capture).
    - ``'+' in san`` but ``board.gives_check(move) is False``.
    - ``'#' in san`` but the resulting position isn't checkmate.
    - Missing ``'+'`` or ``'#'`` when the move actually delivers check /
      checkmate — the SAN is still ambiguous and we require the player to
      announce what they see.

    The board is not mutated. Rationale : ``python-chess`` accepts SAN
    in tolerant mode (``Bxb6`` on an empty ``b6`` parses as ``Bb6``,
    masking a player hallucination of the position — partie 004 round
    16 incident).
    """
    try:
        move = board.parse_san(san)
    except (
        chess.IllegalMoveError,
        chess.InvalidMoveError,
        chess.AmbiguousMoveError,
    ) as exc:
        return ValidationResult(False, f"illegal SAN: {exc}")

    has_x = "x" in san
    has_plus = "+" in san
    has_hash = "#" in san

    is_capture = board.is_capture(move)
    if has_x != is_capture:
        target = chess.square_name(move.to_square)
        return ValidationResult(
            False,
            (
                f"'x' annoncé sans capture réelle (case {target} vide ou même couleur)"
                if has_x
                else f"capture réelle sur {target} sans 'x' dans le SAN"
            ),
        )

    gives_check = board.gives_check(move)

    board.push(move)
    try:
        is_mate = board.is_checkmate()
    finally:
        board.pop()

    if has_hash != is_mate:
        return ValidationResult(
            False,
            (
                "'#' annoncé sans échec et mat réel"
                if has_hash
                else "échec et mat réel sans '#' dans le SAN"
            ),
        )
    if has_plus != gives_check and not has_hash:
        return ValidationResult(
            False,
            ("'+' annoncé sans échec réel" if has_plus else "échec réel sans '+' dans le SAN"),
        )

    return ValidationResult(True)


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


def _validate_game_id(game_id: str) -> None:
    if not GAME_ID_PATTERN.match(game_id):
        raise ChessboardError(f"Invalid game_id {game_id!r}: must match {GAME_ID_PATTERN.pattern}")


def _pgn_path(games_dir: Path, game_id: str) -> Path:
    return games_dir / f"{game_id}.pgn"


def _commentary_path(games_dir: Path, game_id: str) -> Path:
    return games_dir / COMMENTARY_DIR_NAME / f"{game_id}.jsonl"


@dataclass
class GameState:
    """In-memory state of a single game (board + metadata + paths).

    Construct via :py:meth:`create` (new game) or :py:meth:`load`
    (existing PGN). Direct construction is intended only for
    internal use and tests.
    """

    metadata: GameMetadata
    board: chess.Board
    games_dir: Path = GAMES_DIR
    san_history: list[str] = field(default_factory=list)

    # ---- factories ------------------------------------------------------

    @classmethod
    def create(
        cls,
        metadata: GameMetadata,
        *,
        games_dir: Path | None = None,
        starting_fen: str | None = None,
    ) -> GameState:
        """Initialise a new game and persist its empty PGN."""
        _validate_game_id(metadata.game_id)
        games_dir = games_dir or GAMES_DIR
        path = _pgn_path(games_dir, metadata.game_id)
        if path.exists():
            raise GameAlreadyExistsError(str(path))
        board = chess.Board(starting_fen) if starting_fen else chess.Board()
        state = cls(metadata=metadata, board=board, games_dir=games_dir)
        state.save()
        return state

    @classmethod
    def load(
        cls,
        game_id: str,
        *,
        games_dir: Path | None = None,
    ) -> GameState:
        """Load an existing game from its PGN."""
        _validate_game_id(game_id)
        games_dir = games_dir or GAMES_DIR
        path = _pgn_path(games_dir, game_id)
        if not path.exists():
            raise GameNotFoundError(str(path))
        with path.open("r", encoding="utf-8") as f:
            game = chess.pgn.read_game(f)
        if game is None:
            raise ChessboardError(f"PGN at {path} is unreadable")
        headers = dict(game.headers)
        draw_offer_raw = headers.get("DrawOffer") or ""
        draw_offer: dict | None = None
        if ":" in draw_offer_raw:
            side, _, ply_str = draw_offer_raw.partition(":")
            if side in ("white", "black") and ply_str.isdigit():
                draw_offer = {"by": side, "ply": int(ply_str)}
        rated_raw = headers.get("Rated", "").strip().lower()
        metadata = GameMetadata(
            game_id=game_id,
            event=headers.get("Event", ""),
            site=headers.get("Site", ""),
            date=headers.get("Date", ""),
            round=headers.get("Round", "1"),
            white=headers.get("White", ""),
            black=headers.get("Black", ""),
            arbiter=headers.get("Arbiter") or None,
            spectators=([s for s in headers.get("Spectators", "").split(",") if s]),
            time_control=TimeControl(format=headers.get("TimeControl", "unrated")),
            result=headers.get("Result", "*"),
            annotator=headers.get("Annotator") or None,
            opening=headers.get("Opening") or None,
            eco=headers.get("ECO") or None,
            draw_offer=draw_offer,
            termination_note=headers.get("Termination") or None,
            # Preserve the Rated header as-read : an imported PGN with no
            # Rated header stays rated=False after a round-trip, so loading
            # then saving it will NOT suddenly stamp it as rated.
            rated=rated_raw == "true",
        )
        board = game.board()
        san_history: list[str] = []
        for move in game.mainline_moves():
            san_history.append(board.san(move))
            board.push(move)
        return cls(
            metadata=metadata,
            board=board,
            games_dir=games_dir,
            san_history=san_history,
        )

    # ---- mutation -------------------------------------------------------

    def play_move(
        self,
        move_san: str,
        *,
        by: str | None = None,
        expected_ply: int | None = None,
    ) -> dict:
        """Validate a SAN move, push it on the board, persist PGN.

        ``by`` is an optional player identifier used only by the caller's
        audit trail ; the server enforces nothing about turn ownership
        in this skeleton (TODO once arbiter/players are wired up).

        ``expected_ply`` is an optional idempotence guard : if provided
        and the board is no longer at that ply (concurrent writer
        advanced the state), :class:`StalePlyError` is raised **before**
        validation so the caller can re-read and retry. Default
        ``None`` preserves backward-compatible behaviour (no check).
        """
        if expected_ply is not None and expected_ply != self.board.ply():
            raise StalePlyError(current_ply=self.board.ply(), expected_ply=expected_ply)

        strict = validate_san_strict(self.board, move_san)
        if not strict.ok:
            raise IllegalMoveError(
                f"Move {move_san!r} rejected at ply {self.board.ply()}: {strict.reason}"
            )
        mover_side = "white" if self.board.turn == chess.WHITE else "black"
        move = self.board.parse_san(move_san)
        san_normalised = self.board.san(move)
        self.board.push(move)
        self.san_history.append(san_normalised)
        # Playing a move implicitly declines the opponent's pending draw offer.
        if self.metadata.draw_offer and self.metadata.draw_offer["by"] != mover_side:
            self.metadata.draw_offer = None
        if self.board.is_checkmate():
            self.metadata.result = "0-1" if self.board.turn == chess.WHITE else "1-0"
        elif (
            self.board.is_stalemate()
            or self.board.is_insufficient_material()
            or self.board.can_claim_draw()
        ):
            self.metadata.result = "1/2-1/2"
        self.save()
        return {
            "san": san_normalised,
            "ply": self.board.ply(),
            "fen": self.board.fen(),
            "is_check": self.board.is_check(),
            "is_checkmate": self.board.is_checkmate(),
            "is_game_over": self.board.is_game_over(),
            "result": self.metadata.result,
            "by": by,
        }

    def resign(self, by: str) -> dict:
        """Resign the game on behalf of one side.

        ``by`` is either ``"white"`` / ``"black"`` or a player name that
        matches ``metadata.white`` / ``metadata.black``. Updates
        ``metadata.result`` (``"0-1"`` if white resigns, ``"1-0"`` if
        black resigns), records the termination reason in the PGN, and
        clears any pending draw offer. Refuses if the game is already
        over (either via checkmate/stalemate on the board, or via a
        prior resign/draw agreement).
        """
        if self.metadata.result != "*" or self.board.is_game_over():
            raise InvalidGameStateError(
                f"Cannot resign: game already ended (result={self.metadata.result!r})"
            )
        side = self.metadata.resolve_side(by)
        self.metadata.result = "0-1" if side == "white" else "1-0"
        loser = self.metadata.white if side == "white" else self.metadata.black
        self.metadata.termination_note = f"{loser or side} resigns"
        self.metadata.draw_offer = None
        self.save()
        return {
            "resigned_by": side,
            "result": self.metadata.result,
            "termination": self.metadata.termination_note,
            "ply": self.board.ply(),
        }

    def offer_draw(self, by: str) -> dict:
        """Register a draw offer from one side, awaiting agree/decline.

        Idempotent: re-offering from the same side while the offer is
        already pending is a no-op. Refused if the game is already over.
        Refused if the opposite side already has a pending offer — the
        caller should either accept it (``agree_draw``) or decline it
        (``decline_draw``) before making a new one.
        """
        if self.metadata.result != "*" or self.board.is_game_over():
            raise InvalidGameStateError(
                f"Cannot offer draw: game already ended (result={self.metadata.result!r})"
            )
        side = self.metadata.resolve_side(by)
        pending = self.metadata.draw_offer
        if pending and pending["by"] == side:
            return {
                "offered_by": side,
                "ply": pending["ply"],
                "pending": True,
                "idempotent": True,
            }
        if pending and pending["by"] != side:
            raise InvalidGameStateError(
                f"Opposite side ({pending['by']}) already offered draw at "
                f"ply {pending['ply']}: call agree_draw or decline_draw first"
            )
        self.metadata.draw_offer = {"by": side, "ply": self.board.ply()}
        self.save()
        return {
            "offered_by": side,
            "ply": self.metadata.draw_offer["ply"],
            "pending": True,
            "idempotent": False,
        }

    def agree_draw(self, by: str) -> dict:
        """Accept a pending draw offer, ending the game with ``1/2-1/2``.

        Refused if no offer is pending, if the accepting side is the
        same as the offering side (cannot agree with self), or if the
        game is already over.
        """
        if self.metadata.result != "*" or self.board.is_game_over():
            raise InvalidGameStateError(
                f"Cannot agree draw: game already ended (result={self.metadata.result!r})"
            )
        side = self.metadata.resolve_side(by)
        pending = self.metadata.draw_offer
        if not pending:
            raise InvalidGameStateError("Cannot agree draw: no offer pending")
        if pending["by"] == side:
            raise InvalidGameStateError(
                f"Cannot agree draw: offer is from same side ({side}) — the "
                f"other player must accept"
            )
        self.metadata.result = "1/2-1/2"
        self.metadata.termination_note = "draw agreed"
        self.metadata.draw_offer = None
        self.save()
        return {
            "agreed_by": side,
            "offered_by": pending["by"],
            "result": self.metadata.result,
            "termination": self.metadata.termination_note,
            "ply": self.board.ply(),
        }

    def decline_draw(self, by: str) -> dict:
        """Decline a pending draw offer from the opposite side.

        Clears the offer without ending the game. Refused if no offer is
        pending, if the declining side made the offer, or if the game is
        already over.
        """
        if self.metadata.result != "*" or self.board.is_game_over():
            raise InvalidGameStateError(
                f"Cannot decline draw: game already ended (result={self.metadata.result!r})"
            )
        side = self.metadata.resolve_side(by)
        pending = self.metadata.draw_offer
        if not pending:
            raise InvalidGameStateError("Cannot decline draw: no offer pending")
        if pending["by"] == side:
            raise InvalidGameStateError(f"Cannot decline draw: offer is from same side ({side})")
        cleared = dict(pending)
        self.metadata.draw_offer = None
        self.save()
        return {
            "declined_by": side,
            "offered_by": cleared["by"],
            "ply": cleared["ply"],
        }

    def save(self) -> Path:
        """Write the PGN to disk (atomic-ish via tmp + replace)."""
        self.games_dir.mkdir(parents=True, exist_ok=True)
        path = _pgn_path(self.games_dir, self.metadata.game_id)
        tmp = path.with_suffix(".pgn.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for tag, value in self.metadata.to_pgn_headers().items():
                f.write(f'[{tag} "{value}"]\n')
            f.write("\n")
            tail = self.metadata.result
            if self.metadata.termination_note:
                tail = f"{{{self.metadata.termination_note}}} {tail}"
            if self.san_history:
                lines: list[str] = []
                for idx, san in enumerate(self.san_history):
                    if idx % 2 == 0:
                        lines.append(f"{idx // 2 + 1}. {san}")
                    else:
                        lines[-1] += f" {san}"
                f.write(" ".join(lines))
                f.write(f" {tail}\n")
            else:
                f.write(f"{tail}\n")
        tmp.replace(path)
        return path

    # ---- views ----------------------------------------------------------

    def to_state_dict(self) -> dict:
        """Return a compact JSON-serialisable snapshot."""
        return {
            "game_id": self.metadata.game_id,
            "metadata": asdict(self.metadata),
            "fen": self.board.fen(),
            "ply": self.board.ply(),
            "turn": "white" if self.board.turn == chess.WHITE else "black",
            "is_check": self.board.is_check(),
            "is_checkmate": self.board.is_checkmate(),
            "is_game_over": self.board.is_game_over() or self.metadata.result != "*",
            "result": self.metadata.result,
            "san_history": list(self.san_history),
            "draw_offer": self.metadata.draw_offer,
            "termination_note": self.metadata.termination_note,
        }

    def raw_pgn(self) -> str:
        return _pgn_path(self.games_dir, self.metadata.game_id).read_text(encoding="utf-8")


# -- commentary log (independent of GameState) -------------------------------


def post_commentary(
    game_id: str,
    *,
    ply: int,
    source: str,
    text: str,
    games_dir: Path | None = None,
) -> dict:
    """Append one commentary line to the per-game JSONL side-car."""
    _validate_game_id(game_id)
    games_dir = games_dir or GAMES_DIR
    path = _commentary_path(games_dir, game_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "ply": int(ply),
        "source": source,
        "text": text,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False))
        f.write("\n")
    return entry


def get_commentary(
    game_id: str,
    *,
    ply: int | None = None,
    games_dir: Path | None = None,
) -> list[dict]:
    """Return commentary entries (all or filtered by ``ply``)."""
    _validate_game_id(game_id)
    games_dir = games_dir or GAMES_DIR
    path = _commentary_path(games_dir, game_id)
    if not path.exists():
        return []
    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ply is None or entry.get("ply") == ply:
                entries.append(entry)
    return entries


# -- game listing (independent of GameState) ---------------------------------


def list_games(
    games_dir: Path | None = None,
    *,
    player: str | None = None,
    active_only: bool = False,
) -> list[dict]:
    """Enumerate ``<games_dir>/*.pgn`` and return a flat summary list.

    Each entry carries the fields a caller needs to pick a game without
    opening every PGN :

    * ``game_id`` — the file stem
    * ``white`` / ``black`` — player identifiers from the PGN headers
    * ``result`` — ``"*"`` while the game is in progress, else ``"1-0"``
      / ``"0-1"`` / ``"1/2-1/2"``
    * ``ply`` — number of half-moves played
    * ``turn`` — ``"white"`` or ``"black"`` (whose move it is on the live
      board, regardless of ``result``)
    * ``is_game_over`` — ``True`` once the result is set or the position
      is terminal
    * ``mtime`` — PGN file mtime (seconds since epoch, sortable)

    The list is sorted by ``mtime`` descending so the most recently
    touched game bubbles to the top. Malformed PGN files are logged
    and silently skipped — one broken file does not blind a caller to
    the rest of its games.

    ``player`` filters to games where either side matches
    case-insensitively. ``active_only`` restricts to games whose result
    is still ``"*"``.
    """
    gdir = games_dir or GAMES_DIR
    if not gdir.is_dir():
        return []
    needle = player.strip().lower() if player else None
    entries: list[dict] = []
    for pgn in gdir.glob("*.pgn"):
        game_id = pgn.stem
        try:
            state = GameState.load(game_id, games_dir=gdir)
        except (GameNotFoundError, ChessboardError) as exc:
            log.warning("list_games: skip %s (%s)", pgn.name, exc)
            continue
        except Exception as exc:  # noqa: BLE001 — surface any parser breakage.
            log.warning("list_games: skip %s (unexpected: %s)", pgn.name, exc)
            continue
        meta = state.metadata
        if needle is not None and needle not in (meta.white.lower(), meta.black.lower()):
            continue
        if active_only and meta.result != "*":
            continue
        entries.append(
            {
                "game_id": game_id,
                "white": meta.white,
                "black": meta.black,
                "result": meta.result,
                "ply": len(state.san_history),
                "turn": "white" if state.board.turn == chess.WHITE else "black",
                "is_game_over": meta.result != "*" or state.board.is_game_over(),
                "mtime": pgn.stat().st_mtime,
            }
        )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries
