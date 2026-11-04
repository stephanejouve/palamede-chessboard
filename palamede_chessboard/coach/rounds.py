"""What the coach remembers between two rounds of ``palamede-coach run`` (#95).

In memory only : a restart starts afresh, at worst with one full round.

* **mtime filter** — a game settled at a round (reported, in progress,
  no human in it, unreadable) is not even read again while its PGN keeps
  the same mtime. A change of the registry (a new human account) forgets
  them all (Admin, #95).
* **retry delay** — a game that failed (the engine, a store) is tried
  again only after :data:`RETRY_SECONDS`, not at every round : it would
  otherwise redo all its work each time (Admin, #203).
* **blocking games** — a game that waits because live games reached one
  of its positions (:class:`Wait`) is tried again only when one of those
  PGNs changes (a move, the end of the game) or its own does : retried
  every 300 s, it left a ``refused`` line in the engine log each time,
  ~288 a day while a game stayed open (Admin, Leader, 04/11). A wait
  whose blocking games are unknown falls back on the retry delay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from palamede_chessboard.players import registry

#: Seconds before a game that waited or failed is tried again.
RETRY_SECONDS = 300.0
#: Seconds between two rounds of ``palamede-coach run``.
DEFAULT_INTERVAL = 30.0


def mtime_ns(path: Path) -> int | None:
    """``path``'s mtime in ns, ``None`` if it is gone."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


@dataclass(frozen=True)
class Wait:
    """A game that must wait, and the live games it waits for."""

    blockers: frozenset[str] = frozenset()

    @classmethod
    def on(cls, *game_ids: str | None) -> Wait:
        """Waiting on ``game_ids``, the unknown ones (``None``) left out."""
        return cls(frozenset(game_id for game_id in game_ids if game_id))


@dataclass
class RoundMemory:
    #: ``game_id`` → mtime of its PGN when it was settled.
    settled: dict[str, int] = field(default_factory=dict)
    #: ``game_id`` → time before which it is not tried again.
    not_before: dict[str, float] = field(default_factory=dict)
    #: ``game_id`` → mtimes of its own PGN and of each blocking game's.
    blocked: dict[str, dict[Path, int]] = field(default_factory=dict)
    registry_mtime: int | None = None

    def start_round(self, games_dir: Path) -> None:
        """A new round : forget every settled game if the registry changed."""
        mtime = mtime_ns(registry.registry_path(games_dir))
        if mtime != self.registry_mtime:
            self.settled.clear()
            self.blocked.clear()
            self.registry_mtime = mtime

    def unchanged(self, pgn: Path) -> bool:
        """Settled and its PGN not touched since."""
        mtime = self.settled.get(pgn.stem)
        return mtime is not None and mtime == mtime_ns(pgn)

    def deferred(self, game_id: str, now: float) -> bool:
        """Failed less than :data:`RETRY_SECONDS` ago, or waiting on games
        whose PGNs, and its own, have not changed since."""
        watched = self.blocked.get(game_id)
        if watched is not None:
            if all(mtime_ns(path) == mtime for path, mtime in watched.items()):
                return True
            del self.blocked[game_id]
            return False
        return now < self.not_before.get(game_id, 0.0)

    def settle(self, game_id: str, mtime: int | None) -> None:
        """Settled as its PGN was at ``mtime``, read **before** the game :
        a move played meanwhile changes it, and the game is read again."""
        if mtime is not None:
            self.settled[game_id] = mtime
        self.not_before.pop(game_id, None)

    def retry_later(self, game_id: str, now: float) -> None:
        self.not_before[game_id] = now + RETRY_SECONDS

    def wait_for(self, pgn: Path, wait: Wait, mtimes: dict[Path, int | None], now: float) -> None:
        """Wait until ``pgn`` or a blocking game's PGN changes from its mtime
        in ``mtimes``, read at the start of the round : a live game that
        ended while this one was read is seen at the next round, not missed
        for good. On the retry delay if one of them has no mtime."""
        paths = [pgn, *(pgn.with_name(f"{blocker}.pgn") for blocker in wait.blockers)]
        watched = {path: mtimes.get(path) for path in paths}
        if not wait.blockers or None in watched.values():
            self.retry_later(pgn.stem, now)
            return
        self.blocked[pgn.stem] = {path: m for path, m in watched.items() if m is not None}
