"""Server-side guard against engine assistance during in-progress games.

Context : CD tasking 2026-10-02 after Leader used ``chess_analyze_position``
at d22 Stockfish to pick ``22...bxa3`` on ``junior-vs-leader-001`` (he
flagged it and annotated). The honor rule is not enough when a prompt
pushes the other way, so engine tools must refuse on in-progress games
and leave a trace in every case.

Three tools are protected :

* ``chess_analyze_position``
* ``chess_analyze_game``
* ``chess_get_my_mistakes``

``chess_board_facts`` is explicitly out of scope — it stays usable during
games, including the ``#`` marker in ``checks_available``.

Decision rules
--------------

* ``game_id`` + ``result == "*"`` → refused outright (``chess_analyze_*``
  on an in-progress game).
* ``game_id`` + ``result != "*"`` + ``chess_analyze_position`` on a ply
  whose position matches a position reached by an in-progress game
  (after the opening) → refused.
* ``chess_analyze_game`` / ``chess_get_my_mistakes`` on a finished game
  are **not** refused globally — the MCP handler re-filters the walk via
  :func:`mask_walk_entries` and masks only the plies whose position
  matches an in-progress game. Rationale (CD 2026-10-03) : all our games
  share the same openings (1.e4 c5 2.Nf3 d6 3.d4...), refusing wholesale
  would block every Sicilian post-mortem, including GM games.
* Free-form ``fen`` matching an in-progress position (after the opening)
  → refused.

The first :data:`OPENING_PLIES_IGNORED` half-moves of every game are
excluded from the match set so opening-theory analysis stays available.

FEN comparison strips halfmove / fullmove counters and castling rights,
and also compares against the mirror of each in-progress position (color
flip + board rotation) so a trivially-mirrored FEN doesn't escape the
guard.

Audit journal
-------------

Every call — allowed or refused — appends one line to
``~/.local/share/outillages/engine_calls_log.jsonl`` (override via
``OUTILLAGES_ENGINE_CALLS_LOG``). The append is wrapped in
``fcntl.flock(LOCK_EX)`` so concurrent agents don't interleave partial
lines : a journal that loses lines loses its value (CD nit 2026-10-03).
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import chess

from palamede_chessboard.chessboard_state import GameNotFoundError, GameState, list_games

log = logging.getLogger(__name__)

#: Tools gated by this guard. ``chess_board_facts`` is explicitly NOT
#: in this set — the facts tool stays available during games.
GUARDED_TOOLS: frozenset[str] = frozenset(
    {"chess_analyze_position", "chess_analyze_game", "chess_get_my_mistakes"}
)

#: Opening threshold. Positions reached in the first N half-moves of an
#: in-progress game are excluded from the match set. CD 2026-10-03 :
#: without this threshold, every Sicilian post-mortem would be blocked
#: because the first dozen plies are shared across all our games.
OPENING_PLIES_IGNORED: int = 12

#: Classification string stamped on walk entries hidden by the guard.
MASKED_CLASSIFICATION: str = "masked_by_guard"


def engine_calls_log_path() -> Path:
    """Return the per-user audit log path for engine tool calls.

    The log is per-user, like the board-facts journal (same reasoning :
    cross-user permission denied on ``<games_dir>``). Override via env
    var ``OUTILLAGES_ENGINE_CALLS_LOG`` for tests / custom setups.
    """
    override = os.environ.get("OUTILLAGES_ENGINE_CALLS_LOG", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "outillages" / "engine_calls_log.jsonl"


def normalize_fen_for_match(fen: str) -> str | None:
    """Return a short FEN fingerprint (piece placement + side to move).

    Clock counters (halfmove, fullmove) and castling / en-passant rights
    are dropped because a player can trivially tweak them while keeping
    the same position on the board. The fingerprint is what the guard
    compares against the in-progress position set.

    Invalid FENs return ``None`` — the caller treats that as "no match",
    i.e. the engine call stays allowed on malformed input (Stockfish will
    reject the FEN itself).
    """
    try:
        parts = fen.strip().split()
        if len(parts) < 2:
            return None
        placement, side = parts[0], parts[1]
        if side not in ("w", "b"):
            return None
        # Validate placement via python-chess so we don't match on gibberish.
        chess.Board(f"{placement} {side} - - 0 1")
        return f"{placement} {side}"
    except (ValueError, IndexError):
        return None


def _mirror_fingerprint(fingerprint: str) -> str | None:
    """Return the color-flipped, board-rotated fingerprint.

    Catches a trivial evasion where the caller feeds the guard the mirror
    of an in-progress position : same tactics, same evaluation, flipped
    board. ``chess.Board.mirror`` handles both color flip and vertical
    rotation ; we then re-normalize.
    """
    try:
        board = chess.Board(f"{fingerprint} - - 0 1")
    except ValueError:
        return None
    return normalize_fen_for_match(board.mirror().fen())


def _fingerprint_pair(fen: str) -> set[str]:
    """Return ``{fingerprint, mirror}`` for ``fen`` (empty on invalid input)."""
    out: set[str] = set()
    base = normalize_fen_for_match(fen)
    if base is None:
        return out
    out.add(base)
    mirror = _mirror_fingerprint(base)
    if mirror is not None:
        out.add(mirror)
    return out


def _game_position_fingerprints(state: GameState) -> set[str]:
    """Return the set of fingerprints (incl. mirrors) past the opening.

    Only positions reached from ply :data:`OPENING_PLIES_IGNORED` onwards
    are collected. For each such position we also include its mirrored
    fingerprint so a flipped-board attempt still matches. Malformed PGN
    bails out silently and leaves whatever was already collected intact.
    """
    fingerprints: set[str] = set()
    board = chess.Board()
    for san in state.san_history:
        try:
            move = board.parse_san(san)
        except ValueError:
            log.warning("engine_guard: cannot replay %s in %s", san, state.metadata.game_id)
            break
        board.push(move)
        if board.ply() < OPENING_PLIES_IGNORED:
            continue
        fingerprints.update(_fingerprint_pair(board.fen()))
    # Also include ``state.board.fen()`` directly in case the replay bailed
    # out before reaching the current position.
    if state.board.ply() >= OPENING_PLIES_IGNORED:
        fingerprints.update(_fingerprint_pair(state.board.fen()))
    return fingerprints


def _in_progress_positions(games_dir: Path) -> dict[str, set[str]]:
    """Map each in-progress ``game_id`` to its set of post-opening fingerprints."""
    out: dict[str, set[str]] = {}
    for summary in list_games(games_dir, active_only=True):
        game_id = summary["game_id"]
        try:
            state = GameState.load(game_id, games_dir=games_dir)
        except GameNotFoundError:
            continue
        out[game_id] = _game_position_fingerprints(state)
    return out


def _match_against_in_progress(fen: str, in_progress: dict[str, set[str]]) -> str | None:
    """Return the first in-progress ``game_id`` whose fingerprint set contains ``fen``.

    Both the FEN's direct fingerprint and its mirror are checked. Returns
    ``None`` if no in-progress game matches.
    """
    needles = _fingerprint_pair(fen)
    if not needles:
        return None
    for game_id, fingerprints in in_progress.items():
        if needles & fingerprints:
            return game_id
    return None


def _position_fen_at_ply(state: GameState, ply: int) -> str | None:
    """Replay ``state.san_history`` up to ``ply`` and return the resulting FEN.

    ``ply`` is a half-move count (0 = initial position). Returns ``None``
    if the history is too short or contains unreplayable SAN.
    """
    if ply < 0 or ply > len(state.san_history):
        return None
    board = chess.Board()
    for san in state.san_history[:ply]:
        try:
            board.push_san(san)
        except ValueError:
            return None
    return board.fen()


_ALLOWED: dict = {"allowed": True, "matched_game_id": None, "reason": None}


def _refuse_in_progress_game(game_id: str) -> dict:
    return {
        "allowed": False,
        "matched_game_id": game_id,
        "reason": f"partie en cours ({game_id}), outils moteur interdits jusqu'à la fin",
    }


def _refuse_analyze_position_match(game_id: str, target_ply: int, match: str) -> dict:
    return {
        "allowed": False,
        "matched_game_id": match,
        "reason": (
            f"la position du jeu {game_id} au ply {target_ply} correspond à "
            f"une position de la partie en cours {match}, outils moteur interdits"
        ),
    }


def _refuse_fen_match(match: str) -> dict:
    return {
        "allowed": False,
        "matched_game_id": match,
        "reason": (
            f"FEN correspond à une position de la partie en cours "
            f"{match}, outils moteur interdits jusqu'à la fin"
        ),
    }


def _check_against_game_id(
    games_dir: Path,
    *,
    tool: str,
    game_id: str,
    ply: int | None,
    in_progress: dict[str, set[str]],
) -> dict | None:
    """Return refusal dict if ``game_id`` triggers it, else ``None``."""
    try:
        state = GameState.load(game_id, games_dir=games_dir)
    except GameNotFoundError:
        # Downstream tool will raise GAME_NOT_FOUND — guard stays out.
        return _ALLOWED
    if state.metadata.result == "*":
        return _refuse_in_progress_game(game_id)
    # Finished game. Only analyze_position does a single-shot check
    # here ; the walk tools defer to mask_walk_entries.
    if tool != "chess_analyze_position":
        return None
    target_ply = ply if ply is not None else state.board.ply()
    if target_ply < OPENING_PLIES_IGNORED:
        return None
    target_fen = _position_fen_at_ply(state, target_ply)
    if target_fen is None:
        return None
    match = _match_against_in_progress(target_fen, in_progress)
    if match is None:
        return None
    return _refuse_analyze_position_match(game_id, target_ply, match)


def check_engine_call(
    games_dir: Path,
    *,
    tool: str,
    game_id: str | None = None,
    fen: str | None = None,
    ply: int | None = None,
) -> dict:
    """Decide whether an engine tool call is allowed.

    Returns a dict with :

    * ``allowed`` — ``True`` to let the call proceed, ``False`` to refuse.
    * ``matched_game_id`` — the in-progress game that triggered the refusal,
      or ``None``.
    * ``reason`` — human-readable explanation (``None`` when allowed).

    Decision rules :

    * If ``tool`` is not in :data:`GUARDED_TOOLS`, the guard is a no-op.
    * If ``game_id`` is supplied and the game is in progress → refused.
    * For ``chess_analyze_position`` with ``game_id`` + ``ply`` on a
      **finished** game : the single target position (and its mirror) is
      compared to in-progress fingerprints — refused on match. The
      opening threshold is applied at the collection step (in-progress
      fingerprints are only kept past :data:`OPENING_PLIES_IGNORED`), so
      opening theory stays accessible.
    * For ``chess_analyze_game`` / ``chess_get_my_mistakes`` on a
      finished game : **always allowed** here ; per-ply masking happens
      in the MCP handler via :func:`mask_walk_entries`. A global refusal
      would block every Sicilian post-mortem (shared openings).
    * ``fen`` supplied : matched against in-progress fingerprints (incl.
      mirrors). Refused on match.
    """
    if tool not in GUARDED_TOOLS:
        return _ALLOWED

    in_progress = _in_progress_positions(games_dir)

    if game_id:
        verdict = _check_against_game_id(
            games_dir, tool=tool, game_id=game_id, ply=ply, in_progress=in_progress
        )
        if verdict is not None and verdict is not _ALLOWED:
            return verdict
        if verdict is _ALLOWED:
            return _ALLOWED

    if fen:
        match = _match_against_in_progress(fen, in_progress)
        if match is not None:
            return _refuse_fen_match(match)

    return _ALLOWED


def mask_walk_entries(walk: list, games_dir: Path) -> tuple[list, int, list[dict]]:
    """Mask the walk entries whose position matches a live game, past opening.

    ``walk`` is the list of ``GameWalkEntry`` returned by
    :func:`palamede_chessboard.chessboard_engine.analyze_game` (BT-002,
    Leader). For each entry at
    ``ply >= OPENING_PLIES_IGNORED`` whose ``fen_after`` (or mirror)
    appears in an in-progress game's fingerprint set, the entry's
    numeric scores and classification are replaced with ``None`` and
    :data:`MASKED_CLASSIFICATION` respectively.

    Returns a tuple ``(masked_walk, mask_count, mask_details)`` where
    ``mask_details`` is a list of ``{ply, matched_game_id}`` suitable
    for inclusion in the MCP response + audit log.
    """
    in_progress = _in_progress_positions(games_dir)
    if not in_progress:
        return walk, 0, []
    masked_walk = []
    details: list[dict] = []
    for entry in walk:
        if entry.ply < OPENING_PLIES_IGNORED:
            masked_walk.append(entry)
            continue
        match = _match_against_in_progress(entry.fen_after, in_progress)
        if match is None and entry.fen_before:
            match = _match_against_in_progress(entry.fen_before, in_progress)
        if match is None:
            masked_walk.append(entry)
            continue
        # Replace with a shallow copy whose numeric fields are None.
        cls = type(entry)
        masked = cls(
            ply=entry.ply,
            mover=entry.mover,
            san=entry.san,
            fen_before=entry.fen_before,
            fen_after=entry.fen_after,
            score_cp_before=None,
            score_cp_after=None,
            cp_loss=None,
            classification=MASKED_CLASSIFICATION,
        )
        masked_walk.append(masked)
        details.append({"ply": entry.ply, "matched_game_id": match})
    return masked_walk, len(details), details


def log_engine_call(
    *,
    tool: str,
    decision: dict,
    game_id: str | None = None,
    fen: str | None = None,
    caller: str | None = None,
    extra: dict | None = None,
) -> None:
    """Append one line to the per-user engine audit log, protected by flock.

    Called for every guarded tool call — allowed, refused, and partially
    masked — so the log is a complete trail. CD nit 2026-10-03 : the
    append is wrapped in ``fcntl.flock(LOCK_EX)`` so four agents writing
    in parallel never interleave partial lines. ``extra`` carries
    tool-specific context (e.g. mask details from
    :func:`mask_walk_entries`).
    """
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "tool": tool,
        "game_id": game_id,
        "fen": fen,
        "matched_game_id": decision.get("matched_game_id"),
        "decision": "allowed" if decision.get("allowed") else "refused",
        "reason": decision.get("reason"),
        "caller": caller,
    }
    if extra:
        entry.update(extra)
    path = engine_calls_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
