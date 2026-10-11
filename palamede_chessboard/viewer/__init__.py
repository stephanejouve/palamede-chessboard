"""Viewer sub-package (BT-007 split, issue #12).

The pre-split 568-line ``palamede_chessboard.chessboard_viewer``
monolith was carved into one ``APIRouter`` per domain
(``games``/``positions``/``realtime``/``elo``) + a thin
:func:`build_app` assembler, each under the CD budgets
(≤ 500 lines per module, ≤ 80 lines per function).

Public re-exports kept flat so existing callers (tests, uvicorn
entry-point) can import ``build_app`` / ``main`` / ``app`` directly
from the package root. The internal
:func:`palamede_chessboard.viewer.models._game_stream_payload` and
:func:`palamede_chessboard.viewer.app._uvicorn_kwargs` helpers are
also re-exported here because the viewer test suite reaches for them.
"""

from palamede_chessboard.viewer.app import (
    _uvicorn_kwargs,
    app,
    build_app,
    main,
)
from palamede_chessboard.viewer.models import (
    ACCEPTED_VIEWERS,
    DEPTH_OPTIONS,
    PLAYER_VIEWERS,
    TEMPLATES_DIR,
    VIEWER_ROLES,
    CommentaryPayload,
    MovePayload,
    _game_stream_payload,
    _games_dir,
)

__all__ = [
    "ACCEPTED_VIEWERS",
    "CommentaryPayload",
    "DEPTH_OPTIONS",
    "MovePayload",
    "PLAYER_VIEWERS",
    "TEMPLATES_DIR",
    "VIEWER_ROLES",
    "_game_stream_payload",
    "_games_dir",
    "_uvicorn_kwargs",
    "app",
    "build_app",
    "main",
]
