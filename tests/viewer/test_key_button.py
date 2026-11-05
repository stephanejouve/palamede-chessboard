"""The 🔑 button says whose token is held (#229).

Tested as text, like the rest of the viewer : the page asks ``/api/me``
with the token in a header, shows the player with ``textContent``, and the
click keeps asking synchronously so the coach panel sees the new token.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from palamede_chessboard.viewer.models import TEMPLATES_DIR
from tests.viewer.conftest import _page_and_scripts

STATE_JS = (TEMPLATES_DIR / "viewer" / "_state.js.jinja").read_text(encoding="utf-8")


def test_the_player_s_name_has_a_place_next_to_the_button(client: TestClient) -> None:
    page = _page_and_scripts(client)
    assert '<span id="key-who" class="key-who"></span>' in page
    assert "async function checkToken()" in page


def test_the_token_goes_to_api_me_as_a_header_never_in_the_url() -> None:
    assert "fetch('/api/me', { headers: { 'Authorization': 'Bearer ' + token }" in STATE_JS
    for url in re.findall(r"fetch\('([^']*)'", STATE_JS):
        assert "token" not in url


def test_the_name_is_shown_as_text_never_as_html() -> None:
    assert "who.textContent = KEY_WHO.state === 'ok' ? KEY_WHO.player" in STATE_JS
    assert "innerHTML" not in STATE_JS


def test_each_answer_has_its_words() -> None:
    assert "return 'Connecté comme ' + KEY_WHO.player;" in STATE_JS
    assert "if (r.status === 401) next = { state: 'refused', player: '' };" in STATE_JS
    assert "'Jeton refusé.\\nColler un autre jeton" in STATE_JS
    assert "return 'Coller votre jeton de joueur (plm_…) :';" in STATE_JS


def test_an_answer_for_a_token_since_replaced_is_dropped() -> None:
    assert "if (playerToken() !== token) return;" in STATE_JS


def test_the_click_asks_without_waiting_so_the_coach_sees_the_new_token() -> None:
    # The coach panel reloads on the same click, after this listener : an
    # await before the prompt would let it read the old token.
    assert (
        "document.getElementById('key-btn').addEventListener('click', () => {\n"
        "  const value = window.prompt(keyQuestion(), '');"
    ) in STATE_JS
    assert STATE_JS.rstrip().endswith("checkToken();")
