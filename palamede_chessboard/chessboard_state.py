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

import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import chess
import chess.pgn

from palamede_chessboard._atomic import fsync_dir
from palamede_chessboard.chess_arbiter import ValidationResult, validate_san_strict
from palamede_chessboard.commentary import get_commentary, post_commentary
from palamede_chessboard.errors import (
    ChessboardError,
    GameAlreadyExistsError,
    GameNotFoundError,
    IllegalMoveError,
    InvalidGameStateError,
    StalePlyError,
)
from palamede_chessboard.metadata import (
    DEFAULT_TIME_CONTROL_FORMAT,
    GameMetadata,
    TimeControl,
)
from palamede_chessboard.paths import (
    COMMENTARY_DIR_NAME,
    GAME_ID_PATTERN,
    GAMES_DIR,
    _pgn_path,
    _validate_game_id,
)
from palamede_chessboard.pgn_io import metadata_from_headers, pgn_text

__all__ = [
    "COMMENTARY_DIR_NAME",
    "ChessboardError",
    "DEFAULT_TIME_CONTROL_FORMAT",
    "GAMES_DIR",
    "GAME_ID_PATTERN",
    "GameAlreadyExistsError",
    "GameMetadata",
    "GameNotFoundError",
    "GameState",
    "IllegalMoveError",
    "InvalidGameStateError",
    "StalePlyError",
    "TimeControl",
    "ValidationResult",
    "get_commentary",
    "list_games",
    "post_commentary",
    "validate_san_strict",
]

log = logging.getLogger(__name__)


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
    #: The time left to the side that played each move, ``[%clk]`` in the
    #: PGN (#151) ; ``None`` when the game has no clock running.
    clocks: list[float | None] = field(default_factory=list)

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
            raise GameAlreadyExistsError(f"game already exists: {metadata.game_id}")
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
            raise GameNotFoundError(f"game not found: {game_id}")  # never the path (#107)
        with path.open("r", encoding="utf-8") as f:
            game = chess.pgn.read_game(f)
        if game is None:
            log.warning("PGN at %s is unreadable", path)  # the path for the server log only
            raise ChessboardError(f"PGN of {game_id} is unreadable")
        metadata = metadata_from_headers(game_id, dict(game.headers), game.end().comment)
        board = game.board()
        san_history: list[str] = []
        clocks: list[float | None] = []
        for node in game.mainline():
            san_history.append(board.san(node.move))
            clocks.append(node.clock())
            board.push(node.move)
        return cls(
            metadata=metadata,
            board=board,
            games_dir=games_dir,
            san_history=san_history,
            clocks=clocks,
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

        Refuses if the game is already over, as :meth:`resign` does : after a
        resignation, an agreed draw or a fallen flag the board still has legal
        moves, and a mate played then would overturn the result (#299). The
        ply guard fires first, so a stale caller still gets
        :class:`StalePlyError`.
        """
        if expected_ply is not None and expected_ply != self.board.ply():
            raise StalePlyError(current_ply=self.board.ply(), expected_ply=expected_ply)
        if self.metadata.result != "*" or self.board.is_game_over():
            raise InvalidGameStateError(
                f"Cannot play {move_san!r}: game already ended (result={self.metadata.result!r})"
            )

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
        self.clocks.append(None)  # set by the clock, when the game has one
        # Playing a move implicitly declines the opponent's pending draw offer.
        if self.metadata.draw_offer and self.metadata.draw_offer["by"] != mover_side:
            self.metadata.draw_offer = None
        if self.board.is_checkmate():
            self.metadata.result = "0-1" if self.board.turn == chess.WHITE else "1-0"
            self.metadata.termination = "normal"
        elif (
            self.board.is_stalemate()
            or self.board.is_insufficient_material()
            # Only a draw that took place : the third occurrence on the board,
            # fifty real moves. Not ``can_claim_draw()``, also true when the
            # side to move *could* repeat : the server drew for it (#222).
            or self.board.is_repetition(3)
            or self.board.halfmove_clock >= 100
        ):
            self.metadata.result = "1/2-1/2"
            self.metadata.termination = "normal"
        self.save()
        return {
            "san": san_normalised,
            "ply": self.board.ply(),
            "fen": self.board.fen(),
            "is_check": self.board.is_check(),
            "is_checkmate": self.board.is_checkmate(),
            # The result just set, not only what the board alone ends : a
            # threefold or fifty moves is no ``is_game_over()`` of python-chess.
            "is_game_over": self.board.is_game_over() or self.metadata.result != "*",
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
        self.metadata.termination = "normal"
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
        self.metadata.termination = "normal"
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
        # Refused before the tmp file (#128).
        text = pgn_text(self.metadata, self.board.root(), self.san_history, self.clocks)
        with tmp.open("w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())  # durable before the swap (#46)
        tmp.replace(path)
        fsync_dir(path.parent)  # the rename itself (#246)
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
    * ``rated`` / ``date`` — PGN headers, for the viewer's selector (#46)

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
                "rated": meta.rated,
                "date": meta.date,
            }
        )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries
