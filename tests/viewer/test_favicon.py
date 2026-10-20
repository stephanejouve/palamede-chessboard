"""Viewer favicon : a knight on a two-tone square, served as SVG."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("path", ["/favicon.svg", "/favicon.ico"])
def test_favicon_is_served_as_svg(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "max-age=" in r.headers["cache-control"]
    root = ET.fromstring(r.content)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("viewBox") == "0 0 64 64"


def test_page_links_the_favicon(client: TestClient) -> None:
    html = client.get("/").text
    assert '<link rel="icon" type="image/svg+xml" href="/favicon.svg">' in html
