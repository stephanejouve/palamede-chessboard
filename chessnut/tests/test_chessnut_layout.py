"""The board, its tray and the panel ; the clocks beside the moves (smokes of Stéphane, 01-02/11)."""

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
    assert "stage" not in parents.around["side"]


def test_the_clocks_sit_beside_the_moves() -> None:
    """In the panel, on the right of the moves (owner, #173), the opponent's first, the captures and the balance between."""
    parents = Parents()
    parents.feed((PAGE / "index.html").read_text(encoding="utf-8"))
    assert parents.around["moves"][-2:] == ["side", "play"]
    for element in ("clock-top", "clock-bottom"):
        assert parents.around[element][-3:] == ["side", "play", "clocks"], element
    # The captured pieces and, right beside them, the balance, out of the tray
    # (owner #173, review Admin #174).
    for element in ("captures", "balance"):
        assert parents.around[element][-4:] == ["side", "play", "clocks", "taken"], element
    taken = re.search(r"#taken \{(.*?)\}", (PAGE / "app.css").read_text(encoding="utf-8"), re.S)
    assert taken is not None and "display: flex" in taken.group(1)
    # The captured pieces between the two clocks (owner, #173).
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    assert (
        html.index('id="clock-top"') < html.index('id="captures"') < html.index('id="clock-bottom"')
    )
    css = (PAGE / "app.css").read_text(encoding="utf-8")
    play = re.search(r"#play \{(.*?)\}", css, re.S)
    clocks = re.search(r"#clocks \{(.*?)\}", css, re.S)
    assert play is not None and clocks is not None
    assert "display: flex" in play.group(1) and "flex-wrap: wrap" in play.group(1)
    assert "flex-direction: column" in clocks.group(1)
    assert "justify-content: space-between" in clocks.group(1)


def test_the_grid_keeps_the_tray_in_the_boards_column() -> None:
    css = (PAGE / "app.css").read_text(encoding="utf-8")
    wide = re.search(r"main \{\s*display: grid;(.*?)\}", css, re.S)
    assert wide is not None
    assert 'grid-template-areas: "stage side"' in wide.group(1)
    # The board follows the window's height (review Admin #174), room kept for the tray.
    assert "minmax(0, max(240px, calc(100vh - 150px)))" in wide.group(1)
    stage = re.search(r"#stage \{(.*?)\}", css, re.S)
    assert stage is not None and '"board" "tray"' in stage.group(1)
    # Stacked on a narrow window : the board and its tray, then the panel.
    assert 'grid-template-areas: "stage" "side";' in css


def test_the_captured_pieces_are_a_quarter_of_a_square() -> None:
    css = (PAGE / "app.css").read_text(encoding="utf-8")
    piece = re.search(r"#captures piece \{(.*?)\}", css, re.S)
    assert piece is not None
    assert "max(calc(var(--square, 40px) / 4), 22px)" in piece.group(1)


def test_a_coordinate_takes_the_colour_opposite_to_its_square() -> None:
    """a1 dark : rank 1 and file a light on a white board, reversed when flipped (smoke 03/11)."""
    css = (PAGE / "app.css").read_text(encoding="utf-8")
    selector = r"\.cg-wrap\.orientation-(\w+) coords\.(\w+) coord:nth-child\((\w+)\)"
    rules = {
        colour: set(re.findall(selector, selectors))
        for selectors, colour in re.findall(r"([^{}]*)\{\s*color: ([^;]+);", css)
    }
    light = rules["rgba(255, 255, 255, 0.95)"]
    dark = rules["rgba(50, 50, 50, 0.95)"]
    for axis in ("files", "ranks"):
        assert {("white", axis, "odd"), ("black", axis, "even")} <= light
        assert {("white", axis, "even"), ("black", axis, "odd")} <= dark


def test_the_file_letters_clear_the_base_of_the_pieces() -> None:
    # Lower than chessground's -4px, and an id : the rule must win over the vendor's.
    css = (PAGE / "app.css").read_text("utf-8")
    assert "#board coords.files { bottom: -6px; left: 0; text-align: right; }" in css


def test_the_file_letters_stay_in_their_square_on_a_small_board() -> None:
    # Equal cells, not sized by each letter : no drift across the board.
    css = (PAGE / "app.css").read_text("utf-8")
    assert "#board coords.files coord { flex: 1 1 0; padding-right: 3px; }" in css


def test_without_a_game_the_gestures_are_said_under_the_board() -> None:
    """The lobby's text in the tray, where the notes will come ; the panel keeps a short status (Stéphane, 03/11)."""
    parents = Parents()
    parents.feed((PAGE / "index.html").read_text(encoding="utf-8"))
    assert parents.around["lobby"][-2:] == ["stage", "tray"]
    script = (PAGE / "app.js").read_text(encoding="utf-8")
    assert (
        "el('tray').hidden = !fixing && !state.lobby && !state.refused_text && !state.exercise\n"
        "    && el('move-error').hidden;" in script
    )
    assert "el('lobby').textContent = state.lobby || '';" in script
    assert (
        "if (state.lobby) return state.finished ? 'Partie terminée.' : 'Aucune partie.';" in script
    )


def test_the_gestures_help_folds_and_says_the_knight_s_ends() -> None:
    """Smaller, folded once learnt ; its ends are those of the lobby's values (Stéphane, 04/11)."""
    from palamede_chessnut.lobby import CHOICES

    page = (PAGE / "index.html").read_text(encoding="utf-8")
    parents = Parents()
    parents.feed(page)
    assert parents.around["gestures"][-2:] == ["stage", "tray"]
    assert parents.around["tips"][-1] == "stage"
    assert '<details id="gestures" open hidden>' in page
    help_text = page[page.index('<details id="gestures"') : page.index("</details>")]
    minutes, increments = CHOICES[0][1], CHOICES[1][1]
    assert f"de a3 ({minutes[0]} min) à f3 ({minutes[-1]} min)" in help_text
    assert f"de a4 ({increments[0]} s) à f4 ({increments[-1]} s)" in help_text
    script = (PAGE / "app.js").read_text(encoding="utf-8")
    assert "el('gestures').hidden = !state.lobby;" in script
    assert "renderTips(state.tips || {});" in script
    # The board drawn is the status' screen : the board as read in a lobby (#261).
    assert "cg.set({ fen: state.screen });" in script
    assert "cg.set({ fen: state.fen });" not in script


def test_a_refused_move_is_said_under_the_board() -> None:
    # Under the board, where the eye is, not in the side column (smoke of Stéphane, 05/11).
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    tray = html[
        html.index('<section id="tray"') : html.index(
            "</section>", html.index('<section id="tray"')
        )
    ]
    assert '<p id="refused" role="alert" hidden></p>' in tray
    assert '<p id="move-error" role="alert" hidden></p>' in tray  # the mouse's too
    side = html[html.index('<section id="side">') :]
    assert 'id="refused"' not in side and 'id="move-error"' not in side


def test_an_exercise_is_said_under_the_board() -> None:
    # Its family and how to answer it, where the viewer says them (#237).
    parents = Parents()
    parents.feed((PAGE / "index.html").read_text(encoding="utf-8"))
    assert parents.around["exercise"][-2:] == ["stage", "tray"]
    script = (PAGE / "app.js").read_text(encoding="utf-8")
    assert "el('exercise').hidden = !state.exercise;" in script
    assert "el('exercise-text').textContent = state.exercise ? state.exercise.text : '';" in script
    assert "el('exercise-context').hidden = !(state.exercise && state.exercise.context);" in script


def test_an_exercise_puts_its_tray_in_the_right_column() -> None:
    # The board kept shrinking when the exercise's text grew (smoke of
    # Stéphane #238, 06/11) : an exercise has no clock, its tray goes to the
    # right column and the board keeps its full, fixed size.
    js = (PAGE / "app.js").read_text(encoding="utf-8")
    body = js[js.index("function placeTray(exercise)") :]
    body = body[: body.index("\n}\n")]
    assert "const aside = Boolean(exercise) && !NARROW.matches;" in body
    assert "el('side').insertBefore(tray, el('log'))" in body
    assert "el('stage').appendChild(tray)" in body
    # Moved only when its place changes : not re-inserted at each state, which
    # would lay out the page and re-read its role="alert" (review Junior).
    assert "if (aside === (tray.parentElement === el('side'))) return;" in body
    # Its name follows its place (review Junior).
    assert 'tray.setAttribute(\'aria-label\', aside ? "Exercice" : "Sous l\'échiquier");' in body
    # Too narrow for two columns : under the board, as in a game.
    assert "window.matchMedia('(max-width: 760px)')" in js
    assert "NARROW.addEventListener('change', () => placeTray(lastExercise));" in js
    # Placed after each state, once the tray's visibility is known.
    assert (
        "&& el('move-error').hidden;\n  lastExercise = state.exercise;\n  placeTray(state.exercise);"
        in js
    )
    css = (PAGE / "app.css").read_text(encoding="utf-8")
    assert "#tray.aside { grid-area: auto;" in css
    # The board's size no longer depends on the tray.
    assert "--below" not in js and "--below" not in css
