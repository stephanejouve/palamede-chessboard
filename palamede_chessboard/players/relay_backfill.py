"""One-off backfill of the relay teams of games created before #281 (#282, point 6).

The relay games ``chessnut-stephane-008`` to ``-008g`` name their team in
``Black`` only (``relais-leader-admin-junior…``) : no ``WhiteTeam`` /
``BlackTeam``, so no member's token holds the side. The arbiter adds the
headers once, through ``palamede-players relay-teams``.

It is not an editing tool : a game that already has a team is refused, the
members pass the checks of creation (registry, no team inside a team, no
one on both sides), and every backfill is appended to
``<registry dir>/relay_teams.jsonl`` (``0600``).
"""

from __future__ import annotations

import getpass
import os
from datetime import UTC, datetime
from pathlib import Path

from palamede_chessboard.errors import ChessboardError
from palamede_chessboard.players import registry

JOURNAL_FILENAME = "relay_teams.jsonl"


def _os_user() -> str:
    """Who ran the command, for the journal ; never raises.

    In the container the uid has no passwd entry and neither ``USER`` nor
    ``LOGNAME`` is set : :func:`getpass.getuser` raises ``KeyError`` there
    (review Admin #284). The uid is then written.
    """
    for var in ("USER", "LOGNAME"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return f"uid={os.getuid()}"


def backfill_teams(
    games_dir: Path,
    game_id: str,
    white_team: list[str],
    black_team: list[str],
    *,
    dry_run: bool = False,
) -> dict:
    """Write the teams of ``game_id`` once ; :class:`ChessboardError` otherwise.

    With ``dry_run``, everything is checked and nothing is written.
    """
    from palamede_chessboard._mcp_shared import _relay_teams
    from palamede_chessboard.chessboard_state import GameState
    from palamede_chessboard.clock_moves import game_lock
    from palamede_chessboard.players.observe import _append

    if not white_team and not black_team:
        raise ChessboardError("no team given : nothing to backfill")
    with game_lock(game_id, games_dir):
        state = GameState.load(game_id, games_dir=games_dir)
        meta = state.metadata
        if meta.white_team or meta.black_team:
            raise ChessboardError(f"{game_id} already has its teams : backfill refused")
        white, black = _relay_teams(
            {
                "white": meta.white,
                "black": meta.black,
                "white_team": white_team,
                "black_team": black_team,
            },
            games_dir,
        )
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "game_id": game_id,
            "white": meta.white,
            "black": meta.black,
            "white_team": white,
            "black_team": black,
            "os_user": _os_user(),
        }
        if not dry_run:
            meta.white_team, meta.black_team = white, black
            state.save()
            _append(registry.registry_path(games_dir).parent / JOURNAL_FILENAME, entry)
    return {**entry, "dry_run": dry_run}
