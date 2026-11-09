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
    assert "async function loadCoach(again)" in page


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
    assert "find_move: 'Trouvez le bon coup · Tablier ou souris'," in COACH_JS
    assert "attacked: 'Cases attaquées · visionneuse seulement'," in COACH_JS
    assert "escapeHtml(kindLabel(e.kind))" in COACH_JS


def test_the_full_list_is_filtered_by_motif_state_and_form() -> None:
    # Every exercise, the solved ones too (last) : green has its place (#05/11).
    assert "COACH_ALL = COACH_WAITING.concat(list.filter(e => e.state === 'solved'));" in COACH_JS
    assert "Voir tous les exercices (' + num(COACH_ALL.length) + ')" in COACH_JS
    assert "const shown = COACH_ALL.filter(" in COACH_JS
    # Only solved ones left : nothing waiting, but the full list still opens.
    assert (
        "  if (!COACH_ALL.length) return '<div class=\"empty\">Aucun exercice en attente.</div>';"
        in COACH_JS
    )
    assert (
        ": '<div class=\"empty\">Aucun exercice en attente.</div>';\n  return short\n" in COACH_JS
    )
    for name in ("motif", "state", "kind"):
        assert f"filterSelect('{name}'," in COACH_JS
    assert "(!f.motif || e.motif === f.motif)" in COACH_JS
    assert "(!f.state || standing(e) === f.state) && (!f.kind || e.kind === f.kind)" in COACH_JS
    assert "filterSelect('state', 'tous les états', STANDING_LABELS, f.state)" in COACH_JS


def test_the_filter_is_kept_for_the_tab_and_checked_when_read_back() -> None:
    assert "sessionStorage.setItem(FILTER_KEY, JSON.stringify(f))" in COACH_JS
    assert "localStorage" not in COACH_JS
    # A stored value outside the known keys is dropped, never put in the page.
    for name, labels in (
        ("motif", "MOTIF_LABELS"),
        ("state", "STANDING_LABELS"),
        ("kind", "KIND_LABELS"),
    ):
        assert f"{name}: Object.hasOwn({labels}, f.{name}) ? f.{name} : ''," in COACH_JS


def test_a_filter_change_fetches_nothing() -> None:
    body = COACH_JS[COACH_JS.index("function onFilterChange(") :]
    body = body[: body.index("\n}\n")]
    assert "fetch(" not in body and "loadCoach" not in body


def test_the_filter_applies_even_without_session_storage() -> None:
    # Kept in COACH_FILTER and passed along, not read back from storage at
    # each draw (review Admin #233).
    assert "function filteredHtml(f) {" in COACH_JS
    assert "const f = COACH_FILTER = savedFilter();" in COACH_JS
    body = COACH_JS[COACH_JS.index("function onFilterChange(") :]
    body = body[: body.index("\n}\n")]
    assert "const f = COACH_FILTER;" in body and "savedFilter()" not in body
    assert "box.innerHTML = filteredHtml(f);" in body


def test_the_band_sits_under_the_board_outside_the_panel(client: TestClient) -> None:
    # #237 : under the board, where the eye is — not in the panel, where the
    # box went unseen below the list (Stéphane, 05/11).
    page = client.get("/").text
    assert (
        '<div id="board"><div id="move-error"></div></div>\n'
        '      <div class="exercise-band" id="exercise-band" hidden></div>'
    ) in page
    assert "coach-answer" not in COACH_JS.replace("coach-answer-msg", "")


def test_the_band_says_the_family_and_its_rule() -> None:
    assert "title: 'Trouvez le bon coup'," in COACH_JS
    assert "title: 'Cases attaquées'," in COACH_JS
    # An unknown family : no band, rather than an empty one.
    assert "  exerciseBand.hidden = !texts;\n  if (!texts) return;" in COACH_JS
    assert (
        "Votre roi n'est jamais à désigner. Le roi adverse ne compte que contre une pièce non défendue."
        in COACH_JS
    )
    assert "'<div class=\"band-rule\">' + escapeHtml(texts.rule)" in COACH_JS
    assert "showExerciseBand(id, kind, motif, body.game_id);" in COACH_JS


def test_the_rule_of_en_prise_counts_the_exchange() -> None:
    # #303 : « défendue = sauvée » taught a wrong lesson.
    assert "une fois comptés attaquants et défenseurs" in COACH_JS
    assert "moindre valeur" not in COACH_JS


def test_the_answer_box_is_a_form_enter_answers_and_takes_the_focus() -> None:
    assert "(kind === 'attacked'\n      ? '<form id=\"band-answer\">" in COACH_JS
    assert '<button type="submit">Répondre</button></form>' in COACH_JS
    assert (
        "exerciseBand.addEventListener('submit', ev => { ev.preventDefault(); "
        "answerExercise(exerciseBand.dataset.exercise); });"
    ) in COACH_JS
    assert "if (kind === 'attacked') document.getElementById('coach-squares').focus();" in COACH_JS


def test_the_band_follows_the_game_whatever_opened_it() -> None:
    # Leader, 05/11 : the games list, the URL or a reload open an exo.* game
    # too — the band must not need « Lancer ».
    games_js = (TEMPLATES_DIR / "viewer" / "_games_list.js.jinja").read_text(encoding="utf-8")
    assert "  resetAndPoll();\n  refreshExerciseBand(id);\n}" in games_js
    # At load, for the game of the URL.
    assert COACH_JS.rstrip().endswith("\n});\nif (gameId()) refreshExerciseBand(gameId());")
    body = COACH_JS[COACH_JS.index("async function refreshExerciseBand(game)") :]
    body = body[: body.index("\n}\n")]
    assert "  if (game === EXERCISE_GAME) return;\n  hideExerciseBand();" in body
    assert "if (!game.startsWith('exo.') || !playerToken()) return;" in body
    assert "headers: coachHeaders()" in body
    assert "x.game_ids.includes(game)" in body
    # Still this game, and not shown meanwhile by « Lancer » : the typed field stays.
    assert "if (e && gameId() === game && EXERCISE_GAME !== game) showExerciseBand(" in body


def test_a_new_token_reads_the_band_again() -> None:
    block = COACH_JS[COACH_JS.index("document.getElementById('key-btn')") :]
    assert "  hideExerciseBand();\n  if (gameId()) refreshExerciseBand(gameId());" in block


def test_the_band_says_why_this_position_from_the_motif() -> None:
    # Stéphane, 05/11 : at the board, he no longer knew what was expected.
    assert "'\" data-motif=\"' + escapeHtml(e.motif) + '\">Lancer</button></li>'" in COACH_JS
    assert "start.dataset.kind, start.dataset.motif);" in COACH_JS
    for motif in ("missed_mate", "hanging_piece", "ignored_threat"):
        assert f"  {motif}: " in COACH_JS[COACH_JS.index("const EXERCISE_CONTEXTS") :]
    assert "(context ? '<div class=\"band-context\">' + escapeHtml(context)" in COACH_JS


def test_the_band_gives_the_order_first_and_the_mistake_apart() -> None:
    # #301 : « Pièce laissée en prise » in the title was read as what to find.
    body = COACH_JS[COACH_JS.index("function showExerciseBand(") :]
    body = body[: body.index("\n}\n")]
    assert "'<div class=\"band-title\">' + escapeHtml(texts.title)" in body
    assert "motifLabel" not in body
    assert body.index('class="band-title"') < body.index('class="band-rule"')
    assert body.index('class="band-rule"') < body.index('class="band-context"')
    contexts = COACH_JS[COACH_JS.index("const EXERCISE_CONTEXTS") :]
    contexts = contexts[: contexts.index("};")]
    assert contexts.count("Dans votre partie, ") == 3


def test_each_line_says_where_the_player_stands_in_colour_and_words() -> None:
    # Stéphane, 05/11 : missed then found in the same game stays « failed »,
    # like never found ; the attempts tell them apart, an abandon finds nothing.
    assert "if (e.state === 'solved') return 'solved';" in COACH_JS
    assert "if (e.state !== 'failed') return 'todo';" in COACH_JS
    assert "e.attempts.some(a => a && a.ok === true && !a.abandoned)" in COACH_JS
    assert "return found ? 'found' : 'never';" in COACH_JS
    for key, words in (
        ("never", "à reprendre · jamais trouvé"),
        ("found", "à reprendre · déjà trouvé"),
        ("solved", "résolu"),
    ):
        assert f"  {key}: '{words}'," in COACH_JS
    # The colour never alone : the dot is hidden from screen readers, the words stay.
    assert (
        '<span class="dot" aria-hidden="true"></span>\'\n    + escapeHtml(STANDING_LABELS[s])'
        in COACH_JS
    )
    assert "    + standingHtml(e) + '</span>'" in COACH_JS
    # A fresh exercise : no dot, no words, as before.
    assert "  if (s === 'todo') return '';" in COACH_JS


def test_the_colours_come_from_the_theme() -> None:
    css = (TEMPLATES_DIR / "viewer" / "_styles.css.jinja").read_text(encoding="utf-8")
    assert ".st-never .dot { background: var(--warning); }" in css
    assert ".st-found .dot { background: var(--caution); }" in css
    assert ".st-solved .dot { background: var(--ok); }" in css
    assert "--caution: #" in css
