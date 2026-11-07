"""docs/openapi.json stays what the code generates, with every route annotated."""

from __future__ import annotations

import json

from palamede_chessboard.viewer import openapi_doc, openapi_routes


def test_committed_document_matches_the_code() -> None:
    """After an API change : python -m palamede_chessboard.viewer.openapi_doc, then commit."""
    committed = json.loads(openapi_doc.DOC_PATH.read_text(encoding="utf-8"))
    assert committed == openapi_doc.build_spec()


def test_every_route_has_its_annotation() -> None:
    routes = {(m, p) for p, ops in openapi_doc.build_app().openapi()["paths"].items() for m in ops}
    assert routes == set(openapi_routes.DOCS)


def test_json_routes_describe_their_answer_and_others_their_media_type() -> None:
    spec = openapi_doc.build_spec()
    for (method, path), name in openapi_routes.OK_SCHEMAS.items():
        ok = openapi_doc._success(spec["paths"][path][method])
        assert ok["content"]["application/json"]["schema"]["$ref"].endswith(name)
    for (method, path), (media, _schema) in openapi_routes.MEDIA_TYPES.items():
        ok = openapi_doc._success(spec["paths"][path][method])
        assert list(ok["content"]) == [media]


def test_conditional_gets_declare_304_and_etag() -> None:
    spec = openapi_doc.build_spec()
    for method, path in openapi_routes.CONDITIONAL:
        op = spec["paths"][path][method]
        assert "304" in op["responses"]
        assert any(p["name"] == "If-None-Match" for p in op["parameters"])


def test_token_routes_are_secured_and_public_ones_are_not() -> None:
    spec = openapi_doc.build_spec()
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            secured = "security" in op
            assert secured == ((method, path) in openapi_routes.TOKEN_ROUTES), (method, path)
            if secured:
                assert "401" in op["responses"]


def test_rated_is_a_boolean_and_viewer_a_closed_list() -> None:
    spec = openapi_doc.build_spec()
    assert (
        spec["components"]["schemas"]["NewGamePayload"]["properties"]["rated"]["type"] == "boolean"
    )
    engine = spec["paths"]["/api/engine/live"]["get"]["parameters"]
    assert "spectator" in next(p for p in engine if p["name"] == "viewer")["schema"]["enum"]
    # /api/clock does not check viewer (review Leader #264) : no closed list there.
    clock = spec["paths"]["/api/clock"]["get"]["parameters"]
    assert "enum" not in next(p for p in clock if p["name"] == "viewer")["schema"]


def test_creating_a_rated_game_is_a_documented_400() -> None:
    """new_game refuses rated true or a non-boolean with 400 (review Leader #264)."""
    spec = openapi_doc.build_spec()
    assert "400" in spec["paths"]["/api/games"]["post"]["responses"]
