"""Le drag&drop du plateau ne passe jamais par ``viewOnly``.

chessground n'attache ses écouteurs souris qu'à la création, et seulement
si ``viewOnly`` est faux ; ``cg.set()`` ne peut pas changer ``viewOnly``
(src/api.ts : « all config options, except for viewOnly »). Créé en
``viewOnly: true``, le plateau du viewer ne pouvait donc jamais jouer.
Le jeu est ouvert ou fermé par ``movable.color`` seul.
"""

from __future__ import annotations

import re

from palamede_chessboard.viewer.models import TEMPLATES_DIR


def _board_js() -> str:
    return (TEMPLATES_DIR / "viewer" / "_board.js.jinja").read_text()


def test_board_is_created_interactive() -> None:
    init = re.search(r"cg = Chessground\(boardEl, \{(.*?)\n\}\);", _board_js(), re.S)
    assert init is not None
    assert re.search(r"\bviewOnly: false\b", init.group(1))


def test_set_never_toggles_view_only() -> None:
    for call in re.findall(r"cg\.set\(\{(.*?)\}\);", _board_js(), re.S):
        assert "viewOnly" not in call


def test_moves_are_gated_by_movable_color() -> None:
    assert "color: interactive ? turn : undefined" in _board_js()


def test_bounds_are_cleared_at_each_gesture() -> None:
    """#67 : bornes vidées en capture au début de chaque geste, toutes sources."""
    js = _board_js()
    assert "cg.state.dom.bounds.clear()" in js
    loop = re.search(
        r"for \(const type of \[(.*?)\]\) \{\s*boardEl\.addEventListener\(type, (.*?)\);", js, re.S
    )
    assert loop is not None
    assert {t.strip(" '") for t in loop.group(1).split(",")} == {
        "pointerdown",
        "mousedown",
        "touchstart",
    }
    assert "capture: true" in loop.group(2)
