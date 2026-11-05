"""A player's progress (#95, step 2) : read from what the coach stored, no engine.

* **In games** — per motif, the player's mistakes per 100 of their moves,
  the last :data:`WINDOW` finished games against the :data:`WINDOW` before
  (Stéphane, 05/11), with the games and moves counted on each side ;
  ``enough`` is false while either side has fewer than :data:`MIN_GAMES`.
  A game is placed by its ``Date``, not by when it was diagnosed.
* **On exercises** — per motif, the first try on the first presentation :
  the first attempt of the first game launched (Leader, #95), since after
  a 409 the player knows a move that is not it. Solved after a retry, and
  the abandoned games, are counted apart (Stéphane, 05/11).
* **The suggestion** — the oldest proposed exercise of the motif with the
  most mistakes in the last games, else the oldest failed one : its id,
  motif and kind, never its position nor its solution (Admin, #95).
  **The queue** is every waiting exercise's id in that same order, for the
  viewer's short list (#230).
* **The reminder** — exercises waiting for :data:`REMIND_DAYS` days or
  more while none was tried for as long ; in the app only (Stéphane).

Only the token's player's side of a game counts : a game between two
humans has a report for each side (Admin, #95).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import chess

from palamede_chessboard.chessboard_state import ChessboardError, GameState
from palamede_chessboard.coach.motifs import MOTIFS
from palamede_chessboard.coach.store import read_exercises, read_reports

log = logging.getLogger(__name__)

#: Games on each side of the comparison (Stéphane, 05/11).
WINDOW = 10
#: Under it on either side, the comparison says « not enough games » (Leader, #95).
MIN_GAMES = 3
#: Exercises waiting that long, and none tried for as long : the reminder.
REMIND_DAYS = 3


def game_date(state: GameState, now: datetime) -> tuple[str, str]:
    """``(YYYY-MM-DD, source)`` of a game : its ``Date`` header, else ``now``.

    A ``Date`` absent or partial (``????.??.??``) gives the day of the
    diagnosis, right for a game diagnosed in the minute it ended (Leader, #95) ;
    ``now`` is in UTC, and so is that day.
    """
    found = _header_date(state)
    if found is not None:
        return found, "header"
    return now.date().isoformat(), "diagnosis"


def _header_date(state: GameState) -> str | None:
    try:
        return datetime.strptime(state.metadata.date, "%Y.%m.%d").date().isoformat()
    except (TypeError, ValueError):
        return None


def plies_of(state: GameState, side: str) -> int:
    """How many moves ``side`` played in the game."""
    played = len(state.board.move_stack)
    first = state.board.root().turn == chess.WHITE
    mine = (played + 1) // 2 if first else played // 2
    return mine if side == "white" else played - mine


def complete_report(report: dict, state: GameState) -> bool:
    """Add ``game_date`` and the sides' ``plies`` to a report written before
    them ; whether it changed. No engine : the game only.

    Only the ``Date`` header dates such a report : its diagnosis day is not
    the game's (74 games diagnosed the same evening), so an undated game
    gets ``None`` and stays out of the series (Leader, #95).
    """
    changed = False
    if "game_date" not in report:
        header = _header_date(state)
        report["game_date"] = header
        report["game_date_source"] = "header" if header is not None else None
        changed = True
    for side, found in report.get("sides", {}).items():
        if "plies" not in found:
            found["plies"] = plies_of(state, side)
            changed = True
    return changed


def _games_of(games_dir: Path, player_id: str) -> list[dict]:
    """``{date, game_id, plies, by_motif}`` of the player's diagnosed games, latest first."""
    games = []
    for report in read_reports(games_dir):
        for side, found in report.get("sides", {}).items():
            if found.get("player") != player_id:
                continue
            placed = _placed(games_dir, report, side, found)
            if placed is not None:
                games.append({**placed, "by_motif": found.get("by_motif", {})})
    return sorted(games, key=lambda g: (g["date"], g["game_id"]), reverse=True)


def _placed(games_dir: Path, report: dict, side: str, found: dict) -> dict | None:
    """Date and moves of a side ; the PGN for a report written before they were.

    Such a report without a readable ``Date`` stays out of the series.
    """
    game_id = report.get("game_id", "")
    if "game_date" in report and "plies" in found:
        if report["game_date"] is None:  # completed, no ``Date`` : out of the series
            return None
        return {"date": report["game_date"], "game_id": game_id, "plies": found["plies"]}
    try:
        state = GameState.load(game_id, games_dir=games_dir)
    except (ChessboardError, OSError, ValueError) as exc:
        log.info("coach: %s left out of the progress (%s)", game_id, exc)
        return None
    header = _header_date(state)
    if header is None:
        return None
    return {"date": header, "game_id": game_id, "plies": plies_of(state, side)}


def _window(games: list[dict]) -> dict:
    plies = sum(g["plies"] for g in games)
    motifs = {}
    for motif in MOTIFS:
        mistakes = sum(g["by_motif"].get(motif, 0) for g in games)
        per_100 = round(100 * mistakes / plies, 1) if plies else None
        motifs[motif] = {"mistakes": mistakes, "per_100_moves": per_100}
    return {"games": len(games), "moves": plies, "motifs": motifs}


def in_games(games: list[dict]) -> dict:
    """The last :data:`WINDOW` games against the :data:`WINDOW` before."""
    recent, before = games[:WINDOW], games[WINDOW : 2 * WINDOW]
    return {
        "recent": _window(recent),
        "before": _window(before),
        "enough": min(len(recent), len(before)) >= MIN_GAMES,
    }


def first_try(exercise: dict) -> bool | None:
    """The first attempt of the first game launched : ok or not, ``None`` when
    there was none, or the player abandoned before trying (counted apart)."""
    game_ids = exercise.get("game_ids") or []
    if not game_ids:
        return None
    tries = [a for a in exercise.get("attempts", []) if a.get("game_id") == game_ids[0]]
    if not tries or tries[0].get("abandoned"):
        return None
    return bool(tries[0].get("ok"))


def on_exercises(exercises: list[dict]) -> dict:
    """Per motif : states, first tries, solved after a retry, abandoned games."""
    counts: dict[str, dict] = {}
    for exercise in exercises:
        motif = counts.setdefault(
            exercise.get("motif", "other"),
            {
                "exercises": 0,
                "proposed": 0,
                "solved": 0,
                "failed": 0,
                "first_try": {"tried": 0, "ok": 0},
                "solved_after_retry": 0,
                "abandoned_games": 0,
            },
        )
        motif["exercises"] += 1
        state = exercise.get("state")
        if state in ("proposed", "solved", "failed"):
            motif[state] += 1
        first = first_try(exercise)
        if first is not None:
            motif["first_try"]["tried"] += 1
            motif["first_try"]["ok"] += first
        if state == "solved" and first is not True:
            motif["solved_after_retry"] += 1
        motif["abandoned_games"] += sum(
            1 for a in exercise.get("attempts", []) if a.get("abandoned")
        )
    return counts


def queue(exercises: list[dict], games: dict) -> list[str]:
    """The waiting exercises' ids, in the order to play them (#230).

    The proposed ones first, then the failed ones ; within each, the motif
    with the most mistakes in the last games first, then the oldest.
    """
    recent = games["recent"]["motifs"]
    rank = sorted(MOTIFS, key=lambda m: (-recent[m]["mistakes"], MOTIFS.index(m)))

    def order(exercise: dict) -> tuple:
        motif = exercise.get("motif")
        return (rank.index(motif) if motif in rank else len(rank), exercise.get("created_at", ""))

    return [
        e["id"]
        for state in ("proposed", "failed")
        for e in sorted((e for e in exercises if e.get("state") == state), key=order)
        if isinstance(e.get("id"), str)
    ]


def suggestion(exercises: list[dict], games: dict) -> dict | None:
    """The exercise to play next, the head of :func:`queue` : ``{id, motif, kind}`` only."""
    ids = queue(exercises, games)
    if not ids:
        return None
    chosen = next(e for e in exercises if e.get("id") == ids[0])
    return {k: chosen.get(k) for k in ("id", "motif", "kind")}


def _day(stamp: str | None) -> date | None:
    try:
        return datetime.fromisoformat(stamp).date() if stamp else None
    except ValueError:
        return None


def reminder(exercises: list[dict], now: datetime) -> dict:
    """Exercises waiting, the last attempt, and whether to remind."""
    today = now.date()
    waiting = [e for e in exercises if e.get("state") == "proposed"]
    stamps = [a.get("at") for e in exercises for a in e.get("attempts", []) if a.get("at")]
    last = max(stamps, default=None)
    last_day = _day(last)
    since_last = (today - last_day).days if last_day else None
    created = [d for d in (_day(e.get("created_at")) for e in waiting) if d is not None]
    oldest = (today - min(created)).days if created else None
    due = (
        oldest is not None
        and oldest >= REMIND_DAYS
        and (since_last is None or since_last >= REMIND_DAYS)
    )
    return {
        "waiting": len(waiting),
        "last_attempt_at": last,
        "days_since_last_attempt": since_last,
        "due": due,
    }


def progress(games_dir: Path, player_id: str, now: datetime) -> dict:
    """The progress of ``player_id`` ; :class:`~.store.CoachStoreError` when
    their exercises cannot be read."""
    exercises = read_exercises(games_dir, player_id)
    games = in_games(_games_of(games_dir, player_id))
    return {
        "player": player_id,
        "games": games,
        "exercises": on_exercises(exercises),
        "suggestion": suggestion(exercises, games),
        "queue": queue(exercises, games),
        "reminder": reminder(exercises, now),
    }
