"""Per-domain ``APIRouter`` factories for the viewer (BT-007 split).

See each submodule for details :

* :mod:`palamede_chessboard.viewer.routers.games` — listing, raw, move.
* :mod:`palamede_chessboard.viewer.routers.positions` — positions, SVG.
* :mod:`palamede_chessboard.viewer.routers.realtime` — SSE, clock, engine, commentary.
* :mod:`palamede_chessboard.viewer.routers.elo` — rating board.
* :mod:`palamede_chessboard.viewer.routers.players` — who a token is.
"""

from palamede_chessboard.viewer.routers.elo import build_elo_router
from palamede_chessboard.viewer.routers.games import build_games_router
from palamede_chessboard.viewer.routers.players import build_players_router
from palamede_chessboard.viewer.routers.positions import build_positions_router
from palamede_chessboard.viewer.routers.realtime import build_realtime_router

__all__ = [
    "build_elo_router",
    "build_games_router",
    "build_players_router",
    "build_positions_router",
    "build_realtime_router",
]
