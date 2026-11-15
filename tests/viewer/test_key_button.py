"""The 🔑 button says who the session is (#229, #351).

Tested as text, like the rest of the viewer. Since #351 the page holds no
token : 🔑 sends the pasted token once to ``/api/session``, which answers
with an HttpOnly cookie ; ``/api/me`` is then asked with the cookie only,
the player shown with ``textContent``, and « Se déconnecter » expires the
cookie. The browser session itself is tested in ``test_session.py`` and
by the CDP probe of the PR.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from palamede_chessboard.viewer.models import TEMPLATES_DIR
from tests.viewer.conftest import _page_and_scripts

VIEWER = TEMPLATES_DIR / "viewer"
STATE_JS = (VIEWER / "_state.js.jinja").read_text(encoding="utf-8")
SCRIPTS = "\n".join(p.read_text(encoding="utf-8") for p in sorted(VIEWER.glob("*.js.jinja")))


def test_the_player_s_name_and_the_sign_out_have_their_place(client: TestClient) -> None:
    page = _page_and_scripts(client)
    assert '<span id="key-who" class="key-who"></span>' in page
    assert (
        '<button type="button" id="logout-btn" class="logout-btn" hidden>Se déconnecter</button>'
        in page
    )
    assert "async function checkSession()" in page


def test_the_page_holds_no_token() -> None:
    # #351 : no Authorization header, nothing in sessionStorage but the
    # move of an older page's token, never a token in a URL.
    assert "Authorization" not in SCRIPTS
    assert "playerToken" not in SCRIPTS
    assert "sessionStorage.setItem(OLD_TOKEN_KEY" not in STATE_JS
    for url in re.findall(r"fetch\('([^']*)'", SCRIPTS):
        assert "token" not in url


def test_the_pasted_token_goes_once_in_the_body_of_api_session() -> None:
    body = STATE_JS[STATE_JS.index("async function openSession(token)") :]
    body = body[: body.index("\n}\n")]
    assert "fetch('/api/session', {" in body
    assert (
        "method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }),"
        in body
    )
    assert "await checkSession();" in body


def test_an_older_page_s_token_is_moved_then_forgotten() -> None:
    body = STATE_JS[STATE_JS.index("async function startSession()") :]
    body = body[: body.index("\n}\n")]
    assert body.index("sessionStorage.removeItem(OLD_TOKEN_KEY);") < body.index(
        "await openSession(old)"
    )
    assert STATE_JS.rstrip().endswith("startSession();")


def test_signing_out_asks_the_server() -> None:
    # The cookie is HttpOnly : only the server can expire it.
    assert "await fetch('/api/session/logout', { method: 'POST' });" in STATE_JS
    assert (
        "document.getElementById('logout-btn').addEventListener('click', closeSession);" in STATE_JS
    )
    assert "if (out) out.hidden = KEY_WHO.state !== 'ok';" in STATE_JS


def test_the_name_is_shown_as_text_never_as_html() -> None:
    assert "who.textContent = KEY_WHO.state === 'ok' ? KEY_WHO.player" in STATE_JS
    assert "innerHTML" not in STATE_JS


def test_each_answer_has_its_words() -> None:
    assert "return 'Connecté comme ' + KEY_WHO.player;" in STATE_JS
    assert "else if (r.status === 401) next = { state: 'none', player: '' };" in STATE_JS
    assert "'Jeton refusé.\\nColler un autre jeton de joueur (plm_…) :'" in STATE_JS
    assert "return 'Coller votre jeton de joueur (plm_…) :';" in STATE_JS
    # Refused while signed in : the session in place is kept and said so.
    assert (
        "window.alert('Jeton refusé : toujours connecté comme ' + KEY_WHO.player + '.');"
        in STATE_JS
    )


def test_an_answer_overtaken_by_a_newer_check_is_dropped() -> None:
    body = STATE_JS[STATE_JS.index("async function checkSession()") :]
    body = body[: body.index("\n}\n")]
    assert "const mine = ++SESSION_CHECK;" in body
    assert "if (mine !== SESSION_CHECK) return;" in body
    assert body.index("if (mine !== SESSION_CHECK) return;") < body.index("KEY_WHO = next;")


def test_the_other_parts_hear_the_session_once_it_is_known() -> None:
    # The coach and the seat waited on the 🔑 click ; the session is known
    # only after /api/me answers : they listen to its event instead.
    assert "document.dispatchEvent(new Event('palamede-session'));" in STATE_JS
    assert "'key-btn').addEventListener('click', refresh" not in SCRIPTS
    for name in ("_coach.js.jinja", "_game_end.js.jinja"):
        assert "document.addEventListener('palamede-session'," in (VIEWER / name).read_text(
            encoding="utf-8"
        )
