"""The exercise's banner under the board (#237, step 2)."""

from __future__ import annotations

import chess
import pytest
from palamede_chessnut import exercise, viewer
from palamede_chessnut.bridge import Bridge
from palamede_chessnut.status import snapshot as page_state
from test_chessnut import ready_bridge

LIST = {
    "player": "p",
    "exercises": [
        {"id": "a", "kind": "find_move", "motif": "missed_mate", "game_ids": ["exo.p.1"]},
        {
            "id": "b",
            "kind": "attacked",
            "motif": "ignored_threat",
            "game_ids": ["exo.p.2", "exo.p.3"],
        },
        {"id": "c", "kind": "find_move", "game_ids": ["exo.p.4"]},
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
    assert exercise.read(bridge) == {"id": "b", "kind": "attacked", "motif": "ignored_threat"}
    assert gets == [("https://viewer.test/api/coach/exercises", "t")]
    assert page_state(bridge)["exercise"] == {
        "title": "Menace ignorée · Cases attaquées",
        "context": "Dans votre partie, une de vos pièces était déjà en prise ici, "
        "et le coup joué ne l'a pas sauvée.",
        "text": exercise.BANNERS["attacked"][1],
    }


def test_without_a_board_cases_attaquees_stays_in_the_viewer(gets) -> None:
    bridge = exo_bridge("exo.p.3")
    bridge.with_board = False
    exercise.read(bridge)
    assert page_state(bridge)["exercise"]["text"] == exercise.ATTACKED_WITHOUT_BOARD


def test_a_find_move_says_to_play_on_the_board(gets) -> None:
    bridge = exo_bridge("exo.p.1")
    exercise.read(bridge)
    shown = page_state(bridge)["exercise"]
    assert shown["title"] == "Mat manqué · Trouvez le bon coup"
    assert shown["text"].startswith(
        "Trouvez le meilleur coup dans cette position, dans l'app Tablier "
        "ou à la souris dans la visionneuse."
    )


def test_without_a_motif_the_family_alone(gets) -> None:
    bridge = exo_bridge("exo.p.4")
    exercise.read(bridge)
    shown = page_state(bridge)["exercise"]
    assert (shown["title"], shown["context"]) == ("Trouvez le bon coup", "")


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
    bridge.exercise = {"kind": "fork", "motif": "missed_mate"}
    assert page_state(bridge)["exercise"] is None


def test_the_bridge_reads_the_exercise_when_it_runs(monkeypatch) -> None:
    # Review Junior #238 : without this call, no banner at all.
    from palamede_chessnut import bridge as bridge_module
    from palamede_chessnut import clock_hold, token_light

    bridge = Bridge("https://viewer.test", "exo.p.1", chess.WHITE, token="t", with_board=False)
    read: list[Bridge] = []
    monkeypatch.setattr(bridge, "_follow_stream", lambda _loop: None)
    monkeypatch.setattr(token_light, "check", lambda _b: None)
    monkeypatch.setattr(clock_hold, "declare", lambda _b: True)
    monkeypatch.setattr(exercise, "read", read.append)

    async def watched() -> None:
        bridge.stopped.set()

    monkeypatch.setattr(bridge, "watch_game", watched)
    bridge_module.asyncio.run(bridge.run())
    assert read == [bridge]
