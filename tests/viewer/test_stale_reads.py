"""A read sent for the game shown before is dropped when it comes back.

« Lancer » needed two clicks (Stéphane, 05/11) : a read of the previous
exercise, still in flight, came back after the new game opened and drew
the old position ; the new game's read, as long, drew nothing.
"""

from __future__ import annotations

from palamede_chessboard.viewer.models import TEMPLATES_DIR

POLL_JS = (TEMPLATES_DIR / "viewer" / "_poll.js.jinja").read_text(encoding="utf-8")


def _body(name: str) -> str:
    body = POLL_JS[POLL_JS.index(f"async function {name}()") :]
    return body[: body.index("\n}\n")]


def test_positions_of_another_game_are_dropped() -> None:
    body = _body("pollPositions")
    assert "  const game = gameId();\n  if (!game) return;" in body
    # Before anything is said (a 404 of the old game is not « Partie inconnue »)…
    fetched = body.index("await fetch('/api/positions'")
    assert body.index("if (gameId() !== game) return;", fetched) < body.index("r.status === 404")
    # …and before POS is filled.
    read = body.index("await r.json();")
    assert body.index("if (gameId() !== game) return;", read) < body.index("POS = data.positions;")


def test_the_clock_and_the_comments_of_another_game_are_dropped() -> None:
    for name, filled in (
        ("pollClock", "CLOCK = data.clock;"),
        ("pollComments", "COMMENTS = data.entries"),
    ):
        body = _body(name)
        assert "  const game = gameId();\n  if (!game) return;" in body
        read = body.index("await r.json();")
        assert body.index("if (gameId() !== game) return;", read) < body.index(filled)
