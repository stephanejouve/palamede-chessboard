"""The « Mes exercices » panel of the viewer (#95, step 2).

The page is tested as text, like the rest of the viewer : the panel is
there, reads the progress when opened only, sends the token as a header,
and escapes what the server sends.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from palamede_chessboard.viewer.models import TEMPLATES_DIR
from tests.viewer.conftest import _page_and_scripts

COACH_JS = (TEMPLATES_DIR / "viewer" / "_coach.js.jinja").read_text(encoding="utf-8")


def test_the_panel_is_on_the_page_folded(client: TestClient) -> None:
    page = _page_and_scripts(client)
    assert '<details class="coach" id="coach">' in page  # folded : no « open »
    assert "async function loadCoach()" in page


def test_the_progress_is_read_when_the_panel_opens_never_polled() -> None:
    assert (
        "coachBox.addEventListener('toggle', () => { if (coachBox.open) loadCoach(); });"
        in COACH_JS
    )
    assert "setInterval" not in COACH_JS


def test_the_token_goes_as_a_header_never_in_the_url() -> None:
    assert "'Authorization': 'Bearer ' + token" in COACH_JS
    for url in re.findall(r"fetch\('([^']*)'", COACH_JS):
        assert "token" not in url


def test_what_the_server_sends_is_escaped() -> None:
    # Every value put in the HTML goes through escapeHtml, or is a number.
    for name in ("motifLabel(m)", "motifLabel(e.motif)", "e.id", "e.side", "e.kind", "text"):
        assert f"escapeHtml({name})" in COACH_JS


def test_every_motif_has_a_label() -> None:
    from palamede_chessboard.coach.motifs import MOTIFS

    for motif in MOTIFS:
        assert f"  {motif}: '" in COACH_JS


def test_the_server_s_numbers_go_through_num() -> None:
    # Counts and rates are numbers or « — », never raw text in the HTML (review Admin #218).
    assert "Number.isFinite(n) ? String(n) : '—'" in COACH_JS
    for field in (
        "r.waiting",
        "r.days_since_last_attempt",
        "m.per_100_moves",
        "e.first_try.ok",
        "e.first_try.tried",
        "e.abandoned_games",
        "g.recent.games",
        "g.before.games",
    ):
        assert f"num({field})" in COACH_JS
    # The raw forms are gone.
    for raw in (
        "r.waiting + (",
        "' + r.days_since_last_attempt + '",
        "String(m.per_100_moves)",
        "e.first_try.ok + '",
        "e ? e.abandoned_games",
        "'>' + g.recent.games",
        "' + g.before.games + '",
    ):
        assert raw not in COACH_JS
