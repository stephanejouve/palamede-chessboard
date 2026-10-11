"""Compatibility shim re-exporting the viewer sub-package (BT-007 split).

The 568-line god-script was carved into the
:mod:`palamede_chessboard.viewer` sub-package (one ``APIRouter`` per
domain + thin assembler). To preserve every pre-existing import path
(uvicorn entry-point, test suite, external callers), this module
simply re-exports the public API at the historical location.

Prefer the sub-package in new code — this shim stays only for
backward compatibility and will not grow.
"""

from palamede_chessboard.viewer import (
    ACCEPTED_VIEWERS,
    DEPTH_OPTIONS,
    PLAYER_VIEWERS,
    TEMPLATES_DIR,
    VIEWER_ROLES,
    CommentaryPayload,
    MovePayload,
    _game_stream_payload,
    _games_dir,
    _uvicorn_kwargs,
    app,
    build_app,
    main,
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
