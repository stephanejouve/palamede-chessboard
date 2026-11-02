"""The board, its tray and the panel ; the clocks beside the moves (smokes of Stéphane, 01-02/11)."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from palamede_chessnut.page import PAGE


class Parents(HTMLParser):
    """Each element's id → the ids of the elements around it."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str | None] = []
        self.around: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = dict(attrs).get("id")
        if element:
            self.around[element] = [e for e in self.stack if e]
        if tag not in ("input", "br", "img", "meta", "link"):
            self.stack.append(element)

    def handle_endtag(self, tag: str) -> None:
        if tag not in ("input", "br", "img", "meta", "link") and self.stack:
            self.stack.pop()


def test_the_board_and_its_tray_share_one_column() -> None:
    parents = Parents()
    parents.feed((PAGE / "index.html").read_text(encoding="utf-8"))
    for element in ("board", "promo", "tray"):
        assert parents.around[element][-1] == "stage", element
    assert "stage" not in parents.around["side"]


def test_the_clocks_sit_beside_the_moves() -> None:
    """In the panel, on the right of the moves (owner, #173), the opponent's first, the captures between."""
    parents = Parents()
    parents.feed((PAGE / "index.html").read_text(encoding="utf-8"))
    assert parents.around["moves"][-2:] == ["side", "play"]
    # The material balance on the right of the clocks, out of the tray (owner, #173).
    assert parents.around["balance"][-2:] == ["side", "play"]
    for element in ("clock-top", "captures", "clock-bottom"):
        assert parents.around[element][-3:] == ["side", "play", "clocks"], element
    # The captured pieces between the two clocks (owner, #173).
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    assert (
        html.index('id="clock-top"') < html.index('id="captures"') < html.index('id="clock-bottom"')
    )
    css = (PAGE / "app.css").read_text(encoding="utf-8")
    play = re.search(r"#play \{(.*?)\}", css, re.S)
    clocks = re.search(r"#clocks \{(.*?)\}", css, re.S)
    assert play is not None and clocks is not None
    assert "display: flex" in play.group(1) and "flex-wrap: wrap" in play.group(1)
    assert "flex-direction: column" in clocks.group(1)
    assert "justify-content: space-between" in clocks.group(1)


def test_the_grid_keeps_the_tray_in_the_boards_column() -> None:
    css = (PAGE / "app.css").read_text(encoding="utf-8")
    wide = re.search(r"main \{\s*display: grid;(.*?)\}", css, re.S)
    assert wide is not None
    assert 'grid-template-areas: "stage side"' in wide.group(1)
    stage = re.search(r"#stage \{(.*?)\}", css, re.S)
    assert stage is not None and '"board" "tray"' in stage.group(1)
    # Stacked on a narrow window : the board and its tray, then the panel.
    assert 'grid-template-areas: "stage" "side";' in css


def test_the_captured_pieces_are_a_quarter_of_a_square() -> None:
    css = (PAGE / "app.css").read_text(encoding="utf-8")
    piece = re.search(r"#captures piece \{(.*?)\}", css, re.S)
    assert piece is not None
    assert "max(calc(var(--square, 40px) / 4), 22px)" in piece.group(1)
