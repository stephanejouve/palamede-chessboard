"""Derived cache persistence for the Elo ratings.

CD rule 5 : ``ratings.json`` is a derived cache, **never** edited by
hand. :func:`rebuild_and_persist_cache` recomputes from the raw PGNs
and writes the result atomically. :func:`cache_is_stale` compares the
cache's recorded source snapshot (``source_mtime`` + ``source_pgns``)
to the current directory state so the viewer skips rebuilds when the
PGN set has not moved.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from palamede_chessboard.chess_elo.models import RATINGS_FILENAME, RatingBoard
from palamede_chessboard.chess_elo.paths import GAMES_DIR
from palamede_chessboard.chess_elo.pgn_scan import rebuild_ratings_from_games_dir


def ratings_path(games_dir: Path | None = None) -> Path:
    """Where the derived cache lives (never a source of truth)."""
    return (games_dir or GAMES_DIR) / RATINGS_FILENAME


#: Default mode when the cache file is created from scratch. ``0o664`` keeps
#: the file group-writable so the four agents (admin, Leader, Junior, CD)
#: can refresh ``ratings.json`` from their own MCP server without locking
#: each other out. CD regression report 2026-10-03 (outillages#283) : the
#: previous :func:`tempfile.mkstemp` → ``os.replace`` chain stripped the
#: mode down to ``0o600`` because mkstemp applies umask ``0o077``. The
#: explicit ``os.chmod`` below is therefore immunised against the local
#: umask of the viewer process (``0o077`` on the BT-001 install).
#:
#: ``path.parent.mkdir(exist_ok=True)`` on L60, by contrast, DOES inherit
#: the ambient umask — in production the games directory
#: (``/Users/Shared/games``) is pre-created by the install, so the mode
#: of the parent is never exercised here. To keep in mind if the
#: deployment path changes.
_DEFAULT_SHARED_MODE = 0o664


#: Module-level flag so the viewer logs the "cache write failed"
#: warning **once** per process. Re-set on test teardown via a reset
#: fixture if needed.
_cache_write_warned: bool = False


def save_cached_ratings(board: RatingBoard, games_dir: Path | None = None) -> Path:
    """Persist the current board to :data:`RATINGS_FILENAME`, atomically.

    Writes to a sibling temp file in the same directory then calls
    :func:`os.replace` — a reader that opens the file during the write
    sees either the previous content or the new one, never a torn
    partial JSON. Pretty-printed JSON for easy diffing / debugging ;
    **not** read back during normal rebuilds (we re-derive from PGNs).

    **Mode preservation** (CD regression report 2026-10-03,
    outillages#283) : the mode of the existing cache file is captured
    via :func:`os.stat` and reapplied to the tmp sibling **before** the
    atomic replace, so a concurrent reader never sees the mkstemp-default
    ``0o600``. First creation falls back to :data:`_DEFAULT_SHARED_MODE`
    (``0o664`` — group-writable so the triade agents can refresh the
    cache independently of each other's local umask).
    """
    path = ratings_path(games_dir)
    payload = json.dumps(board.to_dict(), ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        prev_mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        prev_mode = _DEFAULT_SHARED_MODE
    # ``delete=False`` so we control the lifetime : we rename it into
    # place or we unlink it ourselves on error.
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{RATINGS_FILENAME}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_f:
            tmp_f.write(payload)
            tmp_f.flush()
            os.fsync(tmp_f.fileno())
        # chmod BEFORE the replace so the atomic swap exposes the right
        # mode to any concurrent reader from the very first instant (no
        # transient 0o600 window).
        os.chmod(tmp_path, prev_mode)
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return path


def load_cached_ratings(games_dir: Path | None = None) -> RatingBoard | None:
    """Read-only helper for display / fast API lookups.

    Returns ``None`` when the cache is missing **or** when its JSON
    cannot be parsed. The latter case covers a torn write caught by a
    concurrent reader even though :func:`save_cached_ratings` is now
    atomic — a stale pre-atomic cache file or a disk corruption still
    deserve a soft-fail. Callers that need fresh data should fall back
    to :func:`rebuild_ratings_from_games_dir`.
    """
    path = ratings_path(games_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return RatingBoard.from_dict(data)


def _snapshot_sources(games_dir: Path) -> tuple[float, list[str]]:
    """Return ``(max_pgn_mtime, sorted_pgn_stems)`` for the current dir.

    Used by both :func:`rebuild_and_persist_cache` (persists this
    snapshot in the resulting board) and :func:`cache_is_stale`
    (compares the current snapshot to the one stored in the cache).

    A PGN that fails to ``stat`` is skipped — not fatal, worst case we
    miss its mtime and will catch it on the next poll if it reappears.
    """
    max_mtime = 0.0
    stems: list[str] = []
    try:
        pgns = list(games_dir.glob("*.pgn"))
    except OSError:
        return max_mtime, stems
    for pgn in pgns:
        try:
            mt = pgn.stat().st_mtime
        except OSError:
            continue
        if mt > max_mtime:
            max_mtime = mt
        stems.append(pgn.stem)
    stems.sort()
    return max_mtime, stems


def cache_is_stale(games_dir: Path | None = None) -> bool:
    """Return ``True`` if the derived cache no longer reflects the PGN dir.

    CD review cycle 3 (outillages#295) : the previous
    ``mtime(cache) vs mtime(games_dir)`` approach required
    :func:`os.utime` after save to prevent the save itself from looking
    like a dir mutation on the next poll. That ``utime`` call fails
    with ``EPERM`` when the viewer runs as a user that does not own
    ``games_dir`` (the real deployment : viewer under ``arbiter``,
    dir under ``magma``). The failure was swallowed, triggering a
    rebuild loop every 30 s per open tab.

    New approach — no utime, no reliance on filesystem permissions :
    store ``source_mtime`` + ``source_pgns`` INSIDE the cache file, and
    compare them to the current state :

    * ``current_max_mtime > cached.source_mtime`` catches updates or
      new games. The comparison is **strict ``>``** on purpose :
      ``>=`` would false-trigger on filesystems with 1 s mtime
      granularity (HFS+ on darwin, ext4 without high-resolution
      timestamps) because the snapshot captured just before the
      rebuild and the mtime of a PGN written just before the snapshot
      can coincide to the second. Strict ``>`` requires at least one
      granularity tick of actual newer writing to invalidate the cache.
      **Known compromise** (CD review cycle 4, outillages#295) : a
      PGN written in the **same second** as the snapshot — i.e. a
      file whose mtime equals ``source_mtime`` — is NOT seen as
      stale on a 1 s-grain filesystem. On HFS+ this means a game
      finishing less than a second before the rebuild captures its
      state but the comparison tie-breaks "equal, not greater",
      leaving the next poll in cache mode until another write bumps
      the mtime. Impact : at worst 30 s of stale rating for that one
      edge-case game (periodic ``pollElo`` catches it), bounded.
      Nanosecond-granularity filesystems (APFS, ext4 with
      ``mount -o nsec``) are immune.
    * ``set(current_stems) != set(cached.source_pgns)`` catches
      deletions / renames / additions independently of mtime.

    Missing cache file or missing ``source_mtime`` field (legacy
    format) → stale by convention so an upgrade triggers one rebuild
    on first access.
    """
    games_dir = games_dir or GAMES_DIR
    cache_path = ratings_path(games_dir)
    if not cache_path.exists():
        return True
    cached = load_cached_ratings(games_dir=games_dir)
    if cached is None or cached.source_mtime is None:
        return True
    current_max, current_stems = _snapshot_sources(games_dir)
    if current_max > cached.source_mtime:
        return True
    return set(current_stems) != set(cached.source_pgns)


def rebuild_and_persist_cache(games_dir: Path | None = None) -> tuple[RatingBoard, bool]:
    """Full rebuild + atomic save, with source state captured in the cache.

    CD review cycle 3 (outillages#295) : the snapshot
    ``(max_mtime, pgn_stems)`` is taken BEFORE the rebuild and stored
    inside the resulting board. A PGN written during the rebuild window
    will land after this snapshot and be detected stale on the next
    poll (its mtime > ``source_mtime``). No ``os.utime`` is needed —
    this works even when the viewer process does not own
    ``games_dir``.

    Returns ``(board, saved)`` — ``saved=False`` if the cache file
    could not be written (log once per process via
    :func:`_warn_cache_write_failure`, caller still gets a fresh board).
    """
    games_dir = games_dir or GAMES_DIR
    pre_mtime, pre_stems = _snapshot_sources(games_dir)
    board = rebuild_ratings_from_games_dir(games_dir=games_dir)
    board.source_mtime = pre_mtime
    board.source_pgns = pre_stems
    saved = False
    try:
        save_cached_ratings(board, games_dir=games_dir)
        saved = True
    except OSError as exc:
        _warn_cache_write_failure(exc)
    return board, saved


def _warn_cache_write_failure(exc: Exception) -> None:
    """Log a single warning per process when the cache file write fails.

    CD review cycle 2 (outillages#295) : the previous ``except OSError:
    log.warning(...)`` on every poll spammed the logs if the FS was
    full. One per process is enough signal.
    """
    global _cache_write_warned
    if _cache_write_warned:
        return
    import logging

    logging.getLogger(__name__).warning(
        "elo cache refresh failed (further occurrences suppressed): %s",
        exc,
    )
    _cache_write_warned = True
