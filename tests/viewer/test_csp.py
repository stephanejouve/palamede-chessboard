"""Content-Security-Policy de la page (#59).

La page détient le jeton du joueur : seul le viewer peut y exécuter du
script ou recevoir des requêtes. Le JS n'est donc plus inline (blocs
<script> ni attributs on*=) : il est servi sous /assets/ avec une
empreinte dans l'URL.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.viewer.app import CONTENT_SECURITY_POLICY


def _directives(header: str) -> dict[str, list[str]]:
    out = {}
    for part in header.split(";"):
        name, *values = part.split()
        out[name] = values
    return out


def test_page_carries_the_policy(client: TestClient) -> None:
    r = client.get("/")
    assert r.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    d = _directives(CONTENT_SECURITY_POLICY)
    assert d["default-src"] == ["'none'"]
    assert d["script-src"] == ["'self'"]
    assert d["connect-src"] == ["'self'"]
    assert d["base-uri"] == ["'none'"]
    assert d["frame-ancestors"] == ["'none'"]
    assert "object-src" not in d  # couvert par default-src 'none'


def test_no_inline_script_in_the_page(client: TestClient) -> None:
    html = client.get("/").text
    for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S):
        assert 'src="/assets/' in attrs
        assert body.strip() == ""
    assert not re.search(r"\son[a-z]+\s*=", html)
    assert "javascript:" not in html


@pytest.mark.parametrize("kind, marker", [("viewer", "function g(a)"), ("board", "import {")])
def test_scripts_are_served_from_assets(client: TestClient, kind: str, marker: str) -> None:
    html = client.get("/").text
    src = re.search(rf'src="(/assets/{kind}\.[0-9a-f]{{12}}\.js)"', html).group(1)
    r = client.get(src)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert "immutable" in r.headers["cache-control"]
    assert marker in r.text
    assert "{{" not in r.text and "{%" not in r.text


def test_board_is_a_module(client: TestClient) -> None:
    html = client.get("/").text
    assert re.search(r'<script type="module" src="/assets/board\.[0-9a-f]{12}\.js">', html)


@pytest.mark.parametrize(
    "path", ["/assets/viewer.js", "/assets/board.000000000000.js", "/assets/..%2Fapp.py"]
)
def test_unknown_assets_are_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_navigation_buttons_use_listeners(client: TestClient) -> None:
    html = client.get("/").text
    assert re.findall(r'data-nav="([spne])"', html) == ["s", "p", "n", "e"]
    viewer_js = client.get(re.search(r'src="(/assets/viewer\.[^"]+)"', html).group(1)).text
    assert "querySelectorAll('[data-nav]')" in viewer_js
