"""Orientation du plateau : les Noirs en bas pour le joueur noir.

Le plateau était figé côté Blancs (``orientation: 'white'``) : le joueur
noir jouait depuis le viewer avec ses pièces en haut. L'orientation suit
maintenant le rôle (``player_black`` → noirs en bas), et un bouton la
retourne à la main ; ce choix reste dans l'URL (``orient``).
"""

from __future__ import annotations

import re

from palamede_chessboard.viewer.models import TEMPLATES_DIR


def _read(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text()


def _function_body(js: str, name: str) -> str:
    body = re.search(rf"function {name}\([^)]*\) \{{(.*?)\n\}}", js, re.S)
    assert body is not None, name
    return body.group(1)


def test_board_is_created_with_the_page_orientation() -> None:
    init = re.search(
        r"cg = Chessground\(boardEl, \{(.*?)\n\}\);", _read("viewer/_board.js.jinja"), re.S
    )
    assert init is not None
    assert "orientation: 'white'" not in init.group(1)
    assert "window.boardOrientation()" in init.group(1)


def test_orientation_follows_the_black_role_unless_flipped() -> None:
    body = _function_body(_read("viewer/_state.js.jinja"), "boardOrientation")
    assert "if (ORIENT) return ORIENT;" in body
    assert "viewerRole() === 'player_black' ? 'black' : 'white'" in body


def test_only_white_or_black_is_read_from_the_url() -> None:
    assert "['white', 'black'].includes(params.get('orient'))" in _read("viewer/_state.js.jinja")


def test_manual_flip_is_kept_in_the_url() -> None:
    body = _function_body(_read("viewer/_poll.js.jinja"), "updateUrl")
    assert "if (ORIENT) p.set('orient', ORIENT);" in body


def test_flip_button_is_wired() -> None:
    assert 'id="flip-btn"' in _read("chessboard_viewer.html.jinja")
    state = _read("viewer/_state.js.jinja")
    handler = re.search(
        r"getElementById\('flip-btn'\)\.addEventListener\('click', \(\) => \{(.*?)\}\);",
        state,
        re.S,
    )
    assert handler is not None
    assert "applyOrientation()" in handler.group(1)


def test_role_change_resets_a_manual_flip() -> None:
    handler = re.search(
        r"roleSelect\.addEventListener\('change', \(\) => \{(.*?)\}\);",
        _read("viewer/_state.js.jinja"),
        re.S,
    )
    assert handler is not None
    assert "ORIENT = '';" in handler.group(1)
    assert "applyOrientation()" in handler.group(1)
