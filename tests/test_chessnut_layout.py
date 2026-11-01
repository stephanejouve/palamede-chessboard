"""The tray stays under the board, however tall the side panel (smoke of Stéphane, 01/11)."""

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
    # The clocks and the panel stay outside : level with the board, not under it.
    for element in ("clock-top", "clock-bottom", "side"):
        assert "stage" not in parents.around[element], element


def test_the_wide_grid_has_no_row_of_its_own_for_the_tray() -> None:
    css = (PAGE / "app.css").read_text(encoding="utf-8")
    wide = re.search(r"main \{\s*display: grid;(.*?)\}", css, re.S)
    assert wide is not None
    assert "tray" not in wide.group(1) and '"stage ctop"' in wide.group(1)
    # Stacked on a narrow window, the column dissolves : clock, then tray.
    assert "#stage { display: contents; }" in css
