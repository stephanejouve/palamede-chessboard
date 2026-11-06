"""« Mes exercices » reads its list once more when an exercise ends (#254).

Tested as text, like the rest of the viewer : the panel kept its opening
read, so a solved exercise stayed « à reprendre » until closed and opened
again. Now the end of the exo.* game shown, or an accepted answer, reads
``/api/coach/exercises`` and ``/api/coach/progress`` once — on the event,
never in a loop — and the list keeps its filter and « Voir tous ».
"""

from __future__ import annotations

from palamede_chessboard.viewer.models import TEMPLATES_DIR

VIEWER = TEMPLATES_DIR / "viewer"
COACH_JS = (VIEWER / "_coach.js.jinja").read_text(encoding="utf-8")
POLL_JS = (VIEWER / "_poll.js.jinja").read_text(encoding="utf-8")


def _body(js: str, head: str) -> str:
    body = js[js.index(head) :]
    return body[: body.index("\n}\n")]


def test_the_end_is_a_change_seen_on_the_game_shown() -> None:
    body = _body(POLL_JS, "async function pollPositions()")
    # Read in progress before, finished now : a first read after resetAndPoll
    # (POS emptied) is not an end, the game may be long over.
    assert "const ended = oldLen > 0 && lastResult === '*' && (data.result || '*') !== '*';" in body
    # Computed before lastResult takes the new result.
    assert body.index("const ended =") < body.index("lastResult = data.result || '*';")
    assert "if (ended && game.startsWith('exo.')) rereadCoach(game);" in body
    # After the guard that drops a read of a game no longer shown.
    assert body.rindex("if (gameId() !== game) return;") < body.index("rereadCoach(game)")


def test_an_accepted_answer_reads_again() -> None:
    body = _body(COACH_JS, "async function answerExercise(id)")
    assert "if (r.ok) { resetAndPoll(); rereadCoach(gameId()); }" in body
    assert body.count("rereadCoach(") == 1


def test_once_per_game_and_only_an_open_panel() -> None:
    body = _body(COACH_JS, "function rereadCoach(game)")
    assert "if (game === COACH_REREAD) return;\n  COACH_REREAD = game;" in body
    assert "if (coachBox.open) loadCoach(true);" in body
    assert "let COACH_REREAD = '';" in COACH_JS


def test_never_in_a_loop() -> None:
    for js in (COACH_JS, POLL_JS):
        assert js.count("rereadCoach(") >= 1
    assert "setInterval" not in COACH_JS
    assert "setTimeout" not in COACH_JS


def test_a_reread_keeps_the_list_on_screen_and_unfolded() -> None:
    body = _body(COACH_JS, "async function loadCoach(again)")
    assert "if (!again) coachBody.innerHTML = '<div class=\"empty\">Chargement…</div>';" in body
    # Read after the fetch, just before the redraw : a fold meanwhile counts.
    assert body.index("const unfolded =") > body.index("await re.json()")
    assert "const unfolded = Boolean(coachBody.querySelector('.coach-all[open]'));" in body
    assert "if (unfolded) coachBody.querySelector('.coach-all').open = true;" in body
    # The filter comes back from the tab's storage at each draw.
    assert "const f = COACH_FILTER = savedFilter();" in COACH_JS


def test_opening_the_panel_still_shows_the_loading_line() -> None:
    assert (
        "coachBox.addEventListener('toggle', () => { if (coachBox.open) loadCoach(); });"
        in COACH_JS
    )
