"""Per-domain ``APIRouter`` factories for the viewer (BT-007 split).

See each submodule for details :

* :mod:`palamede_chessboard.viewer.routers.games` — listing, raw, move.
* :mod:`palamede_chessboard.viewer.routers.positions` — positions, SVG.
* :mod:`palamede_chessboard.viewer.routers.realtime` — SSE, clock, engine, commentary.
* :mod:`palamede_chessboard.viewer.routers.elo` — rating board.
* :mod:`palamede_chessboard.viewer.routers.players` — who a token is.
* :mod:`palamede_chessboard.viewer.routers.coach` — exercises and reports (#95).
* :mod:`palamede_chessboard.viewer.routers.lessons` — lessons (#323).
"""

from palamede_chessboard.viewer.routers.coach import build_coach_router
from palamede_chessboard.viewer.routers.elo import build_elo_router
from palamede_chessboard.viewer.routers.games import build_games_router
from palamede_chessboard.viewer.routers.lessons import build_lessons_router
from palamede_chessboard.viewer.routers.players import build_players_router
from palamede_chessboard.viewer.routers.positions import build_positions_router
from palamede_chessboard.viewer.routers.realtime import build_realtime_router

__all__ = [
    "build_coach_router",
    "build_elo_router",
    "build_games_router",
    "build_lessons_router",
    "build_players_router",
    "build_positions_router",
    "build_realtime_router",
]
