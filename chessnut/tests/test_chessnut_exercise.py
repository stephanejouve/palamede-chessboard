"""The exercise's banner under the board (#237, step 2)."""

from __future__ import annotations

import pytest
from palamede_chessnut import exercise, viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.status import snapshot as page_state
from test_chessnut import ready_bridge

LIST = {
    "player": "p",
    "exercises": [
        {"id": "a", "kind": "find_move", "game_ids": ["exo.p.1"]},
        {"id": "b", "kind": "attacked", "game_ids": ["exo.p.2", "exo.p.3"]},
    ],
}


def exo_bridge(game_id: str) -> Bridge:
    bridge = ready_bridge()
    bridge.game_id = game_id
    return bridge


@pytest.fixture
def gets(monkeypatch) -> list[tuple[str, str]]:
    asked: list[tuple[str, str]] = []

    def get(url: str, token: str, timeout: float = 10) -> tuple[int, dict]:
        asked.append((url, token))
        return 200, LIST

    monkeypatch.setattr(viewer, "get_with_token", get)
    return asked


def test_the_family_is_read_with_the_player_s_token(gets) -> None:
    bridge = exo_bridge("exo.p.3")
    assert exercise.read(bridge) == "attacked"
    assert gets == [("https://viewer.test/api/coach/exercises", "t")]
    assert page_state(bridge)["exercise"] == {
        "title": "Cases attaquées",
        "text": "Cet exercice se fait dans la visionneuse : le Tablier n'envoie que des coups.",
    }


def test_a_find_move_says_to_play_on_the_board(gets) -> None:
    bridge = exo_bridge("exo.p.1")
    exercise.read(bridge)
    assert page_state(bridge)["exercise"]["title"] == "Trouvez le bon coup"


def test_a_game_that_is_no_exercise_asks_nothing(gets) -> None:
    bridge = exo_bridge("tablier.p.1")
    assert exercise.read(bridge) is None
    assert gets == []
    assert page_state(bridge)["exercise"] is None


@pytest.mark.parametrize(
    ("status", "body"), [(404, None), (None, None), (200, {"exercises": "?"}), (200, LIST)]
)
def test_an_old_viewer_or_an_unknown_game_shows_nothing(monkeypatch, status, body) -> None:
    monkeypatch.setattr(viewer, "get_with_token", lambda *_a, **_k: (status, body))
    bridge = exo_bridge("exo.p.9")
    assert exercise.read(bridge) is None
    assert page_state(bridge)["exercise"] is None


def test_an_unknown_family_shows_nothing() -> None:
    bridge = exo_bridge("exo.p.1")
    bridge.exercise_kind = "fork"
    assert page_state(bridge)["exercise"] is None
