"""Filtering, paging and ETag of the viewer's game listing (#46).

``GET /api/games`` used to return every game ever played, reloaded by
each viewer every 30 s. These pure helpers keep the router thin :

* :func:`select_games` — in-progress games (all of them) and one page of
  finished games, filtered by text, player and exercise status ;
* :func:`listing_etag` — an ETag built from a ``stat`` of the PGN files
  only, so an unchanged directory answers 304 without parsing any PGN.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from palamede_chessboard.chess_elo import normalize_identity

#: Finished games returned per page when the caller does not say.
DEFAULT_LIMIT = 20
#: Upper bound of ``limit`` (one page of the selector).
MAX_LIMIT = 100


def _matches(entry: dict, q: str, player: str) -> bool:
    if q and not any(q in str(entry.get(k, "")).lower() for k in ("game_id", "white", "black")):
        return False
    if player and player not in (
        normalize_identity(str(entry.get("white", ""))),
        normalize_identity(str(entry.get("black", ""))),
    ):
        return False
    return True


def select_games(
    entries: list[dict],
    *,
    q: str = "",
    player: str = "",
    exercises: bool = True,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    """Split ``entries`` (as from ``list_games``, newest first) for the selector.

    * ``in_progress`` : every matching game still at ``*`` — never paged,
      and never hidden by ``exercises`` (an exercise being played must
      stay reachable).
    * ``completed`` : matching finished games, ``offset`` / ``limit``
      paged ; ``exercises=False`` drops unrated ones (``rated=false``).
    * ``completed_total`` / ``has_more`` : for the "older games" button.

    ``q`` is a case-insensitive substring of ``game_id`` / ``white`` /
    ``black`` ; ``player`` an identity, compared after normalisation.
    """
    needle = q.strip().lower()
    who = normalize_identity(player) if player.strip() else ""
    kept = [e for e in entries if _matches(e, needle, who)]
    in_progress = [e for e in kept if e.get("result") == "*"]
    completed = [
        e
        for e in kept
        if e.get("result") != "*" and (exercises or e.get("rated", True) is not False)
    ]
    page = completed[offset : offset + limit]
    return {
        "in_progress": in_progress,
        "completed": page,
        "completed_total": len(completed),
        "has_more": offset + len(page) < len(completed),
    }


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 weak comparison of ``If-None-Match`` against ``etag``.

    A reverse proxy (nginx behind DSM) may turn the strong ETag into a
    weak one (``W/"…"``) and the client then sends that form back : a
    strict string equality would never answer 304 (review Admin #52).
    Handles ``*`` and comma-separated lists.
    """
    if not if_none_match:
        return False
    tags = [t.strip() for t in if_none_match.split(",")]
    return "*" in tags or etag.removeprefix("W/") in (t.removeprefix("W/") for t in tags)


def listing_etag(games_dir: Path, query: str) -> str:
    """Quoted ETag of the listing : PGN names, sizes and mtimes, plus the query.

    Only ``stat`` calls : a 304 costs no PGN parsing. Any write to a PGN
    (move, result, new game) changes its mtime or size, hence the ETag.
    """
    h = hashlib.sha256(query.encode())
    if games_dir.is_dir():
        for pgn in sorted(games_dir.glob("*.pgn")):
            try:
                st = pgn.stat()
            except OSError:
                continue
            h.update(f"{pgn.name}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return '"' + h.hexdigest()[:16] + '"'
