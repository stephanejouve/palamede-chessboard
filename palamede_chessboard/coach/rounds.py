"""What the coach remembers between two rounds of ``palamede-coach run`` (#95).

In memory only : a restart starts afresh, at worst with one full round.

* **mtime filter** — a game settled at a round (reported, in progress,
  no human in it, unreadable) is not even read again while its PGN keeps
  the same mtime. A change of the registry (a new human account) forgets
  them all (Admin, #95).
* **retry delay** — a game that waited (a live game reached one of its
  positions) or failed (the engine, a store) is tried again only after
  :data:`RETRY_SECONDS`, not at every round : waiting after the engine
  would otherwise redo all its work each time (Admin, #203).
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


@dataclass
class RoundMemory:
    #: ``game_id`` → mtime of its PGN when it was settled.
    settled: dict[str, int] = field(default_factory=dict)
    #: ``game_id`` → time before which it is not tried again.
    not_before: dict[str, float] = field(default_factory=dict)
    registry_mtime: int | None = None

    def start_round(self, games_dir: Path) -> None:
        """A new round : forget every settled game if the registry changed."""
        mtime = mtime_ns(registry.registry_path(games_dir))
        if mtime != self.registry_mtime:
            self.settled.clear()
            self.registry_mtime = mtime

    def unchanged(self, pgn: Path) -> bool:
        """Settled and its PGN not touched since."""
        mtime = self.settled.get(pgn.stem)
        return mtime is not None and mtime == mtime_ns(pgn)

    def deferred(self, game_id: str, now: float) -> bool:
        """Waited or failed less than :data:`RETRY_SECONDS` ago."""
        return now < self.not_before.get(game_id, 0.0)

    def settle(self, game_id: str, mtime: int | None) -> None:
        """Settled as its PGN was at ``mtime``, read **before** the game :
        a move played meanwhile changes it, and the game is read again."""
        if mtime is not None:
            self.settled[game_id] = mtime
        self.not_before.pop(game_id, None)

    def retry_later(self, game_id: str, now: float) -> None:
        self.not_before[game_id] = now + RETRY_SECONDS
