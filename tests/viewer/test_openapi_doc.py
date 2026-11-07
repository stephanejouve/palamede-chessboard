"""docs/openapi.json stays what the code generates, with every route annotated."""

from __future__ import annotations

import json

from palamede_chessboard.viewer import openapi_doc


def test_committed_document_matches_the_code() -> None:
    """After an API change : python -m palamede_chessboard.viewer.openapi_doc, then commit."""
    committed = json.loads(openapi_doc.DOC_PATH.read_text(encoding="utf-8"))
    assert committed == openapi_doc.build_spec()


def test_every_route_has_its_annotation() -> None:
    routes = {(m, p) for p, ops in openapi_doc.build_app().openapi()["paths"].items() for m in ops}
    assert routes == set(openapi_doc.DOCS)


def test_json_routes_describe_their_answer_and_others_their_media_type() -> None:
    spec = openapi_doc.build_spec()
    for (method, path), name in openapi_doc.OK_SCHEMAS.items():
        ok = openapi_doc._success(spec["paths"][path][method])
        assert ok["content"]["application/json"]["schema"]["$ref"].endswith(name)
    for (method, path), (media, _schema) in openapi_doc.MEDIA_TYPES.items():
        ok = openapi_doc._success(spec["paths"][path][method])
        assert list(ok["content"]) == [media]


def test_conditional_gets_declare_304_and_etag() -> None:
    spec = openapi_doc.build_spec()
    for method, path in openapi_doc.CONDITIONAL:
        op = spec["paths"][path][method]
        assert "304" in op["responses"]
        assert any(p["name"] == "If-None-Match" for p in op["parameters"])


def test_token_routes_are_secured_and_public_ones_are_not() -> None:
    spec = openapi_doc.build_spec()
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            secured = "security" in op
            assert secured == ((method, path) in openapi_doc.TOKEN_ROUTES), (method, path)
            if secured:
                assert "401" in op["responses"]


def test_rated_is_a_boolean_and_viewer_a_closed_list() -> None:
    spec = openapi_doc.build_spec()
    assert (
        spec["components"]["schemas"]["NewGamePayload"]["properties"]["rated"]["type"] == "boolean"
    )
    clock = spec["paths"]["/api/clock"]["get"]["parameters"]
    viewer = next(p for p in clock if p["name"] == "viewer")
    assert "spectator" in viewer["schema"]["enum"]
