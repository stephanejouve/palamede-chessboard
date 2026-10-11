"""FastAPI assembler + uvicorn launcher for the viewer (BT-007 split).

Carved out of the pre-split 568-line ``chessboard_viewer.py``. The
module exposes :

* :func:`build_app` — construct the ``FastAPI`` instance, mount the
  domain routers (``games`` / ``positions`` / ``realtime`` / ``elo``)
  and register the index + health root routes.
* :func:`_uvicorn_kwargs` — pure env-var → kwargs mapping, kept
  separate so the unit test can assert the resulting dict without
  booting a server.
* :func:`main` — CLI entry-point ``palamede-chessboard-viewer``.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from palamede_chessboard.chessboard_engine import DEFAULT_DEPTH
from palamede_chessboard.viewer.models import (
    DEPTH_OPTIONS,
    TEMPLATES_DIR,
    VIEWER_ROLES,
)
from palamede_chessboard.viewer.routers import (
    build_elo_router,
    build_games_router,
    build_positions_router,
    build_realtime_router,
)

log = logging.getLogger(__name__)


def build_app() -> FastAPI:
    """Return a freshly-instantiated FastAPI viewer app (test-friendly).

    The function stays an assembler : it only wires the per-domain
    ``APIRouter`` factories under the FastAPI root (so the URL
    contract from the pre-split monolith is preserved byte-for-byte)
    and registers the two root routes (``/`` index + ``/api/health``)
    that depend on the ``Jinja2Templates`` instance owned here.
    """
    app = FastAPI(title="Chess ChessBoard Viewer", version="2.0-phase-c")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/")
    def index(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "chessboard_viewer.html.jinja",
            {
                "viewer_roles": VIEWER_ROLES,
                "depth_options": DEPTH_OPTIONS,
                "default_depth": DEFAULT_DEPTH,
            },
        )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(build_games_router())
    app.include_router(build_positions_router())
    app.include_router(build_realtime_router())
    app.include_router(build_elo_router())
    return app


app = build_app()


def _uvicorn_kwargs() -> dict:
    """Build the ``uvicorn.run`` kwargs from env vars (test-friendly).

    Environment variables read :

    * ``MCP_CHESSBOARD_VIEWER_HOST`` (default ``127.0.0.1``).
    * ``MCP_CHESSBOARD_VIEWER_PORT`` (default ``8765``).
    * ``MCP_CHESSBOARD_VIEWER_FORWARDED_IPS`` (default ``127.0.0.1``) :
      liste d'IPs reverse proxy autorisées à pousser ``X-Forwarded-*``.
      Mettre ``*`` pour faire confiance à n'importe quel proxy
      (acceptable en LAN privé fermé), ou lister explicitement les IPs
      reverse proxy (ex. ``192.168.1.78`` pour DSM).
    """
    return {
        "app": "palamede_chessboard.viewer.app:app",
        "host": os.environ.get("MCP_CHESSBOARD_VIEWER_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MCP_CHESSBOARD_VIEWER_PORT", "8765")),
        "reload": False,
        "proxy_headers": True,
        "forwarded_allow_ips": os.environ.get("MCP_CHESSBOARD_VIEWER_FORWARDED_IPS", "127.0.0.1"),
    }


def main() -> None:
    """CLI entry point ``palamede-chessboard-viewer`` (uvicorn launcher).

    Quand le viewer tourne derrière un reverse proxy (ex. DSM
    ``*.alliancejr.eu``), ``proxy_headers=True`` permet à uvicorn de
    lire ``X-Forwarded-Proto`` / ``X-Forwarded-For`` /
    ``X-Forwarded-Host`` et de loguer le vrai client IP au lieu de
    l'IP du proxy.
    """
    import uvicorn

    uvicorn.run(**_uvicorn_kwargs())


if __name__ == "__main__":
    main()
