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
        "\">' + g.recent.games",  # the double quote of the original (Leader, #221)
        "' + g.before.games + '",
    ):
        assert raw not in COACH_JS


def test_the_short_list_follows_the_server_s_queue() -> None:
    # The order comes from /progress (#230), the page only places the ids.
    assert "COACH_WAITING = inQueueOrder(list, progress.queue);" in COACH_JS
    assert "const SHORT_LIST = 5;" in COACH_JS
    assert "COACH_WAITING.slice(0, SHORT_LIST).map((e, i) => exerciseItem(e, i === 0))" in COACH_JS
    assert "exercisesHtml(exercises, progress)" in COACH_JS


def test_each_line_says_its_form() -> None:
    # « Attacked » is answered here, not on the Tablier (smoke of #226, Admin).
    assert "find_move: 'Trouve le bon coup · Tablier ou souris'," in COACH_JS
    assert "attacked: 'Cases attaquées · visionneuse seulement'," in COACH_JS
    assert "escapeHtml(kindLabel(e.kind))" in COACH_JS


def test_the_full_list_is_filtered_by_motif_state_and_form() -> None:
    assert "Voir tous les exercices (' + num(COACH_WAITING.length) + ')" in COACH_JS
    for name in ("motif", "state", "kind"):
        assert f"filterSelect('{name}'," in COACH_JS
    assert "(!f.motif || e.motif === f.motif)" in COACH_JS
    assert "(!f.state || e.state === f.state) && (!f.kind || e.kind === f.kind)" in COACH_JS


def test_the_filter_is_kept_for_the_tab_and_checked_when_read_back() -> None:
    assert "sessionStorage.setItem(FILTER_KEY, JSON.stringify(f))" in COACH_JS
    assert "localStorage" not in COACH_JS
    # A stored value outside the known keys is dropped, never put in the page.
    for name, labels in (("motif", "MOTIF_LABELS"), ("state", "STATE_LABELS"), ("kind", "KIND_LABELS")):
        assert f"{name}: Object.hasOwn({labels}, f.{name}) ? f.{name} : ''," in COACH_JS


def test_a_filter_change_fetches_nothing() -> None:
    body = COACH_JS[COACH_JS.index("function onFilterChange(") :]
    body = body[: body.index("\n}\n")]
    assert "fetch(" not in body and "loadCoach" not in body
