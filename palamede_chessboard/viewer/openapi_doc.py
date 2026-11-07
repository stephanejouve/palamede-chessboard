"""OpenAPI description of the viewer's HTTP API, versioned in ``docs/openapi.json``.

FastAPI generates the skeleton from the code (``build_app().openapi()`` :
routes, parameters, request bodies). This module adds what the code does
not say : the bearer token, the error codes, the response shapes (the
handlers return plain dicts, so FastAPI only knows ``{}``), the content
types of the non-JSON routes, the 304 of the conditional GETs, and a French
summary per route. The data lives in :mod:`.openapi_schemas` (response
shapes) and :mod:`.openapi_routes` (per-route annotations).

Regenerate after an API change, and commit the result :

    poetry run python -m palamede_chessboard.viewer.openapi_doc

``tests/viewer/test_openapi_doc.py`` fails while ``docs/openapi.json`` differs
from what this module builds, and when a route has no annotation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from palamede_chessboard import __version__
from palamede_chessboard.viewer.app import build_app
from palamede_chessboard.viewer.models import VIEWER_ROLES
from palamede_chessboard.viewer.openapi_routes import (
    CONDITIONAL,
    DESCRIPTION,
    DOCS,
    ERRORS,
    EXTRA_ERRORS,
    MEDIA_TYPES,
    NO_403,
    OK_SCHEMAS,
    SERVER,
    TAGS,
    TOKEN_ROUTES,
)
from palamede_chessboard.viewer.openapi_schemas import SCHEMAS, ref

PIECE_PARAMS = {"color": ["w", "b"], "type": ["p", "n", "b", "r", "q", "k"]}

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def _success(op: dict) -> dict:
    code = next((c for c in op["responses"] if c.startswith("2")), "200")
    return op["responses"][code]


def _annotate(method: str, path: str, op: dict) -> None:
    key = (method, path)
    op["summary"], op["description"], tag = DOCS[key]
    op["tags"] = [tag]
    ok = _success(op)
    if key in OK_SCHEMAS:
        ok["content"] = {"application/json": {"schema": ref(OK_SCHEMAS[key])}}
    if key in MEDIA_TYPES:
        media, schema = MEDIA_TYPES[key]
        ok["content"] = {media: {"schema": schema}}
    if key in CONDITIONAL:
        ok["headers"] = {
            "ETag": {"schema": {"type": "string"}, "description": "à renvoyer en If-None-Match"}
        }
        op.setdefault("parameters", []).append(
            {
                "name": "If-None-Match",
                "in": "header",
                "required": False,
                "schema": {"type": "string"},
            }
        )
        op["responses"]["304"] = {
            "description": "Inchangé depuis l'ETag envoyé, sans corps",
            "headers": {"ETag": {"schema": {"type": "string"}}},
        }
    for param in op.get("parameters", []):
        # Only the engine route checks viewer (400 otherwise) ; /api/clock echoes it.
        # piece.svg checks color and type (400 otherwise) : closed lists too.
        if path == "/api/piece.svg" and param["name"] in PIECE_PARAMS:
            param["schema"] = {"type": "string", "enum": PIECE_PARAMS[param["name"]]}
        if param["name"] == "viewer" and path == "/api/engine/live":
            param["schema"] = {"type": "string", "enum": list(VIEWER_ROLES), "default": "spectator"}
    codes = list(EXTRA_ERRORS.get(key, []))
    if key in TOKEN_ROUTES:
        op["security"] = [{"jetonJoueur": []}]
        codes += ["401", "503"] if key in NO_403 else ["401", "403", "503"]
    error = {"application/json": {"schema": ref("Error")}}
    for code in sorted(set(codes)):
        op["responses"].setdefault(code, {"description": ERRORS[code], "content": error})


def build_spec() -> dict:
    """The annotated OpenAPI document ; KeyError for a route without annotation."""
    spec = build_app().openapi()
    spec["info"] = {
        "title": "API palamède (viewer)",
        "version": __version__,
        "description": DESCRIPTION,
    }
    spec["servers"] = [{"url": SERVER, "description": "Production"}]
    components = spec.setdefault("components", {})
    components["securitySchemes"] = {
        "jetonJoueur": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "plm_…",
            "description": "Jeton d'un joueur du registre palamède. Jamais dans l'URL.",
        }
    }
    schemas = components.setdefault("schemas", {})
    schemas.update(SCHEMAS)
    # rated is checked by hand in new_game (a strict bool) : the model says object.
    schemas["NewGamePayload"]["properties"]["rated"] = {
        "type": "boolean",
        "default": False,
        "description": "Parties non classées seulement : true, ou un non-booléen, donne 400.",
    }
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            _annotate(method, path, op)
    spec["tags"] = [{"name": t} for t in TAGS]
    return spec


def render(spec: dict) -> str:
    return json.dumps(spec, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    DOC_PATH.write_text(render(build_spec()), encoding="utf-8")
    print(f"{DOC_PATH} écrit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
