"""Tests for app-level viewer concerns : HTML index rendering + uvicorn kwargs."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.viewer.conftest import _page_and_scripts


class TestHtmlIndex:
    """Phase D : root path serves the Jinja2-rendered HTML UI."""

    def test_root_returns_html(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = _page_and_scripts(client)
        assert "<title>Chess Viewer</title>" in body
        assert "/api/board.svg" in body  # JS references the endpoint
        assert "/api/engine/live" in body
        assert "/api/clock" in body

    def test_role_select_renders_all_viewer_roles(self, client: TestClient) -> None:
        # Jinja2 refactor : the role <select> is now generated from
        # VIEWER_ROLES — verify each role appears as an <option>.
        from palamede_chessboard.chessboard_viewer import VIEWER_ROLES

        body = client.get("/").text
        for role in VIEWER_ROLES:
            assert f'<option value="{role}">{role}</option>' in body

    def test_engine_depth_select_marks_default(self, client: TestClient) -> None:
        # Jinja2 refactor : depth options are looped from DEPTH_OPTIONS, and
        # DEFAULT_DEPTH must carry the ``selected`` attribute.
        from palamede_chessboard.chessboard_engine import DEFAULT_DEPTH
        from palamede_chessboard.chessboard_viewer import DEPTH_OPTIONS

        body = client.get("/").text
        for d in DEPTH_OPTIONS:
            if d == DEFAULT_DEPTH:
                assert f'<option value="{d}" selected>{d}</option>' in body
            else:
                assert f'<option value="{d}">{d}</option>' in body


class TestUvicornKwargs:
    """Reverse-proxy aware uvicorn launcher (PR follow-up Phase D)."""

    def test_defaults_safe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "MCP_CHESSBOARD_VIEWER_HOST",
            "MCP_CHESSBOARD_VIEWER_PORT",
            "MCP_CHESSBOARD_VIEWER_FORWARDED_IPS",
        ):
            monkeypatch.delenv(var, raising=False)
        from palamede_chessboard.chessboard_viewer import _uvicorn_kwargs

        kw = _uvicorn_kwargs()
        assert kw["host"] == "127.0.0.1"
        assert kw["port"] == 8765
        assert kw["proxy_headers"] is True
        assert kw["forwarded_allow_ips"] == "127.0.0.1"

    def test_env_overrides_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_CHESSBOARD_VIEWER_HOST", "0.0.0.0")
        monkeypatch.setenv("MCP_CHESSBOARD_VIEWER_PORT", "9001")
        monkeypatch.setenv("MCP_CHESSBOARD_VIEWER_FORWARDED_IPS", "192.168.1.78")
        from palamede_chessboard.chessboard_viewer import _uvicorn_kwargs

        kw = _uvicorn_kwargs()
        assert kw["host"] == "0.0.0.0"
        assert kw["port"] == 9001
        assert kw["forwarded_allow_ips"] == "192.168.1.78"
        assert kw["proxy_headers"] is True

    def test_wildcard_forwarded_ips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_CHESSBOARD_VIEWER_FORWARDED_IPS", "*")
        from palamede_chessboard.chessboard_viewer import _uvicorn_kwargs

        kw = _uvicorn_kwargs()
        assert kw["forwarded_allow_ips"] == "*"
