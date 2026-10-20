"""The viewer template is split into includes (#46) : the page must still
render every part, from the installed package's template directory."""

from __future__ import annotations

from fastapi.testclient import TestClient

from palamede_chessboard.viewer.models import TEMPLATES_DIR

#: One marker per include, taken from its content.
MARKERS = {
    "_styles.css": ".wrap",
    "_state.js": "const params = new URLSearchParams(location.search);",
    "_games_list.js": "function selectGame(id)",
    "_poll.js": "async function pollPositions()",
    "_render.js": "function renderCaptures(fen)",
    "_live.js": "function startEventStream()",
    "_board.js": "import { Chessground }",
}


def test_every_include_is_rendered(client: TestClient) -> None:
    page = client.get("/").text
    missing = [name for name, marker in MARKERS.items() if marker not in page]
    assert missing == []
    assert "{%" not in page and "{{" not in page


def test_includes_exist_in_template_dir() -> None:
    names = {p.name for p in (TEMPLATES_DIR / "viewer").glob("*.jinja")}
    assert names == {f"{name}.jinja" for name in MARKERS}
