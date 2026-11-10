"""The reply waits for the clock (#316) : the click that starts it, or the clock switched off."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard import chessboard_clock
from palamede_chessboard.chessboard_clock import init_and_save_clock, load_clock
from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.clock_moves import clock_after_move
from palamede_chessboard.players import registry
from palamede_chessboard.viewer.models import TEMPLATES_DIR

CLOCK_JS = (TEMPLATES_DIR / "viewer" / "_clock_control.js.jinja").read_text(encoding="utf-8")
GAME = "clock-click"


def _create(games_dir: Path, game_id: str, fmt: str = "15+10") -> None:
    tc = TimeControl(format=fmt)
    meta = GameMetadata(game_id=game_id, white="alice", black="bob", time_control=tc)
    GameState.create(meta, games_dir=games_dir)
    init_and_save_clock(game_id, tc, games_dir=games_dir)


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid in ("alice", "bob", "carol"):
        registry.add_player(games_dir, pid, kind="human")
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    _create(games_dir, GAME)
    return issued


def _move(client: TestClient, san: str, token: str, game: str = GAME):
    return client.post(
        f"/api/games/{game}/move", json={"san": san}, headers={"Authorization": f"Bearer {token}"}
    )


def _clock(client: TestClient, action: str, token: str | None, game: str = GAME):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(f"/api/games/{game}/clock/{action}", headers=headers)


def _stored(games_dir: Path, game: str = GAME):
    return load_clock(game, games_dir=games_dir)


def test_the_first_move_is_played_but_the_reply_waits(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    assert _move(client, "e4", tokens["alice"]).status_code == 200
    refused = _move(client, "e5", tokens["bob"])
    assert refused.status_code == 409 and "Pendule non démarrée" in refused.json()["detail"]
    assert GameState.load(GAME, games_dir=games_dir).san_history == ["e4"]


def test_once_clicked_the_reply_is_played(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    _move(client, "e4", tokens["alice"])
    assert _clock(client, "start", tokens["alice"]).status_code == 200
    assert _move(client, "e5", tokens["bob"]).status_code == 200


def test_clicked_before_the_first_move_white_runs(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    body = _clock(client, "start", tokens["alice"]).json()
    assert body["status"] == "ok"
    assert (body["clock"]["started"], body["clock"]["active"]) == (True, "white")


def test_clicked_after_the_first_move_the_side_to_move_runs(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    _move(client, "e4", tokens["alice"])
    assert _clock(client, "start", tokens["bob"]).json()["clock"]["active"] == "black"


def test_a_second_click_restarts_nothing(
    client: TestClient, games_dir: Path, tokens: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chessboard_clock, "_now", lambda: 1000.0)
    _clock(client, "start", tokens["alice"])
    _move(client, "e4", tokens["alice"])
    monkeypatch.setattr(chessboard_clock, "_now", lambda: 1500.0)
    again = _clock(client, "start", tokens["bob"]).json()["clock"]
    assert (again["active"], again["last_move_ts"]) == ("black", 1000.0)


def test_a_tablier_game_starts_on_its_first_move(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    _create(games_dir, "tablier.clock")
    assert _move(client, "e4", tokens["alice"], game="tablier.clock").status_code == 200
    assert _move(client, "e5", tokens["bob"], game="tablier.clock").status_code == 200


def test_a_game_without_clock_is_not_held(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    _create(games_dir, "no-clock", fmt="unrated")
    _move(client, "e4", tokens["alice"], game="no-clock")
    assert _move(client, "e5", tokens["bob"], game="no-clock").status_code == 200


def test_switched_off_the_game_goes_on_without_clock(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    body = _clock(client, "off", tokens["bob"]).json()
    assert body["clock"]["off_by"] == "bob" and body["clock"]["started"] is False
    _move(client, "e4", tokens["alice"])
    assert _move(client, "e5", tokens["bob"]).status_code == 200
    assert "%clk" not in (games_dir / f"{GAME}.pgn").read_text(encoding="utf-8")


def test_the_opponent_reads_who_switched_it_off(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    _clock(client, "off", tokens["bob"])
    seen = client.get(f"/api/clock?game_id={GAME}").json()["clock"]
    assert seen["off_by"] == "bob"


def test_switched_off_a_tablier_game_does_not_start(games_dir: Path, tokens: dict) -> None:
    _create(games_dir, "tablier.off")
    clock = _stored(games_dir, "tablier.off")
    clock.off_by = "alice"
    chessboard_clock.save_clock(clock, games_dir=games_dir)
    state = GameState.load("tablier.off", games_dir=games_dir)
    state.play_move("e4")
    clock_after_move(state, "white", games_dir)
    assert _stored(games_dir, "tablier.off").started is False


@pytest.mark.parametrize(
    ("setup", "action"),
    [
        ("move", "off"),  # too late : a move was played
        ("start", "off"),  # too late : the clock runs
        ("off", "start"),  # switched off : final
        ("resign", "start"),  # the game is over
        ("resign", "off"),
    ],
)
def test_what_can_no_longer_be_done_is_409(
    client: TestClient, games_dir: Path, tokens: dict, setup: str, action: str
) -> None:
    if setup == "move":
        _move(client, "e4", tokens["alice"])
    elif setup == "resign":
        GameState.load(GAME, games_dir=games_dir).resign(by="white")
    else:
        _clock(client, setup, tokens["alice"])
    assert _clock(client, action, tokens["bob"]).status_code == 409


@pytest.mark.parametrize("action", ["start", "off"])
def test_a_game_without_clock_has_nothing_to_set(
    client: TestClient, games_dir: Path, tokens: dict, action: str
) -> None:
    _create(games_dir, "no-clock", fmt="unrated")
    assert _clock(client, action, tokens["alice"], game="no-clock").status_code == 409


def test_switching_off_twice_keeps_the_first(
    client: TestClient, games_dir: Path, tokens: dict
) -> None:
    _clock(client, "off", tokens["bob"])
    assert _clock(client, "off", tokens["alice"]).json()["clock"]["off_by"] == "bob"


@pytest.mark.parametrize(
    ("action", "who", "status"),
    [("start", None, 401), ("off", None, 401), ("start", "carol", 403), ("off", "carol", 403)],
)
def test_only_a_player_or_the_arbiter_may_set_it(
    client: TestClient, games_dir: Path, tokens: dict, action: str, who: str | None, status: int
) -> None:
    assert _clock(client, action, tokens[who] if who else None).status_code == status
    assert _stored(games_dir).started is False and _stored(games_dir).off_by is None


@pytest.mark.parametrize("action", ["start", "off"])
def test_a_missing_game_is_404(client: TestClient, tokens: dict, action: str) -> None:
    assert _clock(client, action, tokens["alice"], game="ghost-game").status_code == 404


def test_the_page_has_the_note_and_the_script() -> None:
    page = (TEMPLATES_DIR / "chessboard_viewer.html.jinja").read_text(encoding="utf-8")
    assert '<div class="clock-note" id="clock-note" hidden></div>' in page
    bundle = (TEMPLATES_DIR / "viewer.js.jinja").read_text(encoding="utf-8")
    # GAME_END is read by the clock's click : its script loads first.
    assert bundle.index("_game_end.js.jinja") < bundle.index("_clock_control.js.jinja")
    assert bundle.index("_clock_control.js.jinja") < bundle.index("_live.js.jinja")


def test_the_click_is_offered_to_a_player_of_a_live_game_only() -> None:
    assert "!CLOCK.started && !CLOCK.off_by && colors.length && live" in CLOCK_JS
    assert "if (clockSettable()) clockAction('start');" in CLOCK_JS


def test_switching_off_is_offered_before_the_first_move_and_confirmed() -> None:
    assert 'firstMove ? \' <button type="button" data-clock="off">' in CLOCK_JS
    assert "window.confirm('Jouer cette partie sans pendule ?" in CLOCK_JS
    assert "Pendule coupée par <strong>' + escapeHtml(CLOCK.off_by)" in CLOCK_JS


def test_the_note_follows_the_clock_and_the_seat() -> None:
    poll = (TEMPLATES_DIR / "viewer" / "_poll.js.jinja").read_text(encoding="utf-8")
    end = (TEMPLATES_DIR / "viewer" / "_game_end.js.jinja").read_text(encoding="utf-8")
    assert "renderClocks();\n    renderClockControl();" in poll
    assert "function renderGameEnd() {\n  renderClockControl();" in end


def test_the_arbiter_may_start_it(client: TestClient, games_dir: Path, tokens: dict) -> None:
    tc = TimeControl(format="15+10")
    meta = GameMetadata(
        game_id="refereed", white="alice", black="bob", arbiter="carol", time_control=tc
    )
    GameState.create(meta, games_dir=games_dir)
    init_and_save_clock("refereed", tc, games_dir=games_dir)
    assert _clock(client, "start", tokens["carol"], game="refereed").status_code == 200
