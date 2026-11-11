"""docs/mcp-tools.json stays what the code generates ; every HTTP route has its MCP twin or a reason (#269)."""

from __future__ import annotations

import json
from pathlib import Path

from palamede_chessboard import mcp_tools_doc
from palamede_chessboard._mcp_tool_schemas import TOOL_SCHEMAS
from palamede_chessboard.viewer import openapi_doc, openapi_routes

TOOL_NAMES = [t.name for t in TOOL_SCHEMAS]


def test_committed_document_matches_the_code() -> None:
    """After a tool change : python -m palamede_chessboard.mcp_tools_doc, then commit."""
    committed = json.loads(mcp_tools_doc.DOC_PATH.read_text(encoding="utf-8"))
    assert committed == mcp_tools_doc.build_doc()


def test_write_produces_the_committed_bytes(tmp_path: Path) -> None:
    out = tmp_path / "mcp-tools.json"
    mcp_tools_doc.write(out)
    assert out.read_bytes() == mcp_tools_doc.DOC_PATH.read_bytes()


def test_every_http_route_has_its_tool_or_its_reason() -> None:
    """The routes are those of docs/openapi.json (same source as test_openapi_doc)."""
    routes = {(m, p) for p, ops in openapi_doc.build_app().openapi()["paths"].items() for m in ops}
    assert routes == set(openapi_routes.DOCS)
    with_tool, without = set(mcp_tools_doc.ROUTE_TOOLS), set(mcp_tools_doc.ROUTES_WITHOUT_TOOL)
    assert with_tool.isdisjoint(without)
    assert with_tool | without == routes


def test_every_tool_has_its_route_or_its_reason() -> None:
    twins = list(mcp_tools_doc.ROUTE_TOOLS.values())
    assert len(twins) == len(set(twins)), "one tool, one route"
    assert set(twins) <= set(TOOL_NAMES)
    without = set(mcp_tools_doc.TOOLS_WITHOUT_ROUTE)
    assert without.isdisjoint(twins)
    assert set(twins) | without == set(TOOL_NAMES)


def test_every_reason_is_said() -> None:
    for why in [
        *mcp_tools_doc.ROUTES_WITHOUT_TOOL.values(),
        *mcp_tools_doc.TOOLS_WITHOUT_ROUTE.values(),
    ]:
        assert why.strip().endswith("."), why


def test_the_document_lists_the_tools_in_their_order_with_their_schema() -> None:
    doc = mcp_tools_doc.build_doc()
    assert [t["name"] for t in doc["tools"]] == TOOL_NAMES
    for entry, tool in zip(doc["tools"], TOOL_SCHEMAS, strict=True):
        assert entry["inputSchema"] == tool.inputSchema
        assert entry["description"] == tool.description
        assert (entry["http"] is None) == ("http_note" in entry)
    assert doc["routes"] == sorted(mcp_tools_doc.route_label(m, p) for m, p in openapi_routes.DOCS)
    assert set(doc["routes_without_tool"]) <= set(doc["routes"])


def test_the_twins_are_the_same_operation() -> None:
    """Spot checks read in the handlers : a move, a seat, the clock, a lesson."""
    twin = mcp_tools_doc.http_twin
    assert twin("chess_play_move") == "POST /api/games/{game_id}/move"
    assert twin("chess_seat") == "GET /api/games/{game_id}/seat"
    assert twin("chess_clock_state") == "GET /api/clock"
    assert twin("chess_create_lesson") == "POST /api/lessons"
    assert twin("chess_board_facts") is None


def test_no_version_in_the_document() -> None:
    """No field for release-please to bump (#271) : the tools do not change at a release."""
    assert "version" not in json.dumps(mcp_tools_doc.build_doc())
