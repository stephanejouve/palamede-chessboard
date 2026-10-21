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


def _function_body(js: str, name: str) -> str:
    body = re.search(rf"function {name}\([^)]*\) \{{(.*?)\n\}}", js, re.S)
    assert body is not None, name
    return body.group(1)


def test_refused_move_restores_the_full_server_state() -> None:
    """#67 : un coup refusé laissait ``turnColor`` au camp adverse.

    chessground bascule ``turnColor`` dès le lâcher (``baseUserMove``) ;
    remettre la seule FEN rendait les pièces du joueur inertes, et aucun
    coup suivant ne partait. Le retour arrière réapplique l'état serveur
    complet (FEN, trait, couleur jouable), annule geste et pré-coup, et
    efface la surbrillance du coup refusé.
    """
    js = _board_js()
    restore = _function_body(js, "restoreBoard")
    assert "window.__cgSetFen(...cgLast)" in restore
    assert "cg.cancelMove()" in restore
    assert "cg.cancelPremove()" in restore
    assert "lastMove: undefined" in restore
    assert "cgLast = [fen, turn, interactive];" in js
    drop = _function_body(js, "onUserDrop")
    # Les deux chemins d'échec (réponse refusée, erreur réseau) passent par là.
    assert drop.count("restoreBoard();") == 2
    assert "cg.set({ fen:" not in drop


def test_premoves_are_disabled() -> None:
    """Le viewer ne joue jamais de pré-coup : il n'en propose donc pas (#67)."""
    init = re.search(r"cg = Chessground\(boardEl, \{(.*?)\n\}\);", _board_js(), re.S)
    assert init is not None
    assert "premovable: { enabled: false }" in init.group(1)


def test_move_error_stays_until_the_next_attempt() -> None:
    """Le bandeau d'erreur reste lisible jusqu'au coup suivant (#67)."""
    js = _board_js()
    assert "setTimeout" not in _function_body(js, "showMoveError")
    assert "hideMoveError();" in _function_body(js, "onUserDrop")
