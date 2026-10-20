"""chessground embarqué (#59) : servi par le viewer, jamais par un CDN.

La page détient le jeton du joueur ; un script d'une origine tierce
pourrait le lire. Les fichiers sont figés : leurs empreintes sont celles
de l'archive npm chessground 9.2.1 (voir le README du répertoire).
"""

from __future__ import annotations

import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.viewer.models import CHESSGROUND_DIR, CHESSGROUND_VERSION
from tests.viewer.conftest import _page_and_scripts

SHA256 = {
    "chessground.min.js": "915457e1a6b84abd9778d3ea5317a9fccaf59f4caa78196efbd92f586c0f0e9b",
    "chessground.base.css": "77cbfd65f9b3f64f310a0b8eab0e34ef9f364172e4f678fc05c114b385a341a6",
    "chessground.brown.css": "80de613fe928a947e88b983835286f21c723738d9319b1f8868442bb56fb1577",
    "chessground.cburnett.css": "f9216192b5383a83e62a81de1cf58520f7c251f6f24930f5710a3c797f78ced4",
    "LICENSE": "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
    "package.json": "d81e01041b7bf64bc7e5d77a84b46f0b7f1fb7ab9d41e6b260ace9c33ea8f28e",
}
#: Empreinte de src/ : sha256 des (nom, contenu) triés par nom.
SRC_SHA256 = "d8ecbba0e8c10e2118011e86b4e1015d073df83a0ab34780e25d6845784031ff"
BASE = f"/vendor/chessground@{CHESSGROUND_VERSION}"


def test_embedded_files_are_the_npm_ones() -> None:
    names = {f.name for f in CHESSGROUND_DIR.iterdir()} - {"README.md", "src"}
    assert names == set(SHA256)
    for name, digest in SHA256.items():
        assert hashlib.sha256((CHESSGROUND_DIR / name).read_bytes()).hexdigest() == digest, name


def test_corresponding_source_is_shipped() -> None:
    """GPL : la source TypeScript du .min.js est livrée avec lui."""
    src = CHESSGROUND_DIR / "src"
    assert (src / "chessground.ts").is_file()
    h = hashlib.sha256()
    for f in sorted(src.iterdir()):
        h.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    assert h.hexdigest() == SRC_SHA256


def test_licence_is_shipped_next_to_the_code() -> None:
    assert "GNU GENERAL PUBLIC LICENSE" in (CHESSGROUND_DIR / "LICENSE").read_text()


@pytest.mark.parametrize(
    "name, media_type",
    [
        ("chessground.min.js", "text/javascript"),
        ("chessground.base.css", "text/css"),
        ("chessground.brown.css", "text/css"),
        ("chessground.cburnett.css", "text/css"),
    ],
)
def test_files_are_served(client: TestClient, name: str, media_type: str) -> None:
    r = client.get(f"{BASE}/{name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(media_type)
    assert "immutable" in r.headers["cache-control"]
    assert hashlib.sha256(r.content).hexdigest() == SHA256[name]


@pytest.mark.parametrize(
    "name",
    ["LICENSE", "README.md", "package.json", "src", "..%2Fmodels.py", "chessground.js", "x.css"],
)
def test_only_the_listed_assets_are_served(client: TestClient, name: str) -> None:
    assert client.get(f"{BASE}/{name}").status_code == 404


def test_other_version_is_not_served(client: TestClient) -> None:
    assert client.get("/vendor/chessground@9.2.0/chessground.min.js").status_code == 404


def test_page_loads_nothing_from_a_third_party_origin(client: TestClient) -> None:
    html = _page_and_scripts(client)
    assert not re.search(r"""(src|href)=["']?(https?:)?//""", html)
    assert not re.search(r"""from\s+["'](https?:)?//""", html)
    assert f"from '{BASE}/chessground.min.js'" in html
    for css in ("base", "brown", "cburnett"):
        assert f'<link rel="stylesheet" href="{BASE}/chessground.{css}.css">' in html
