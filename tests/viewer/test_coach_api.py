"""The coach's API in the viewer : exercises, their games, reports (#95).

Players named by function, exercises stored directly : the engine is
not needed here, the solution was computed when the exercise was drawn.
"""

from __future__ import annotations

import json
from pathlib import Path

import chess
import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.coach import play
from palamede_chessboard.coach.store import (
    add_exercises,
    player_path,
    read_exercises,
    write_report,
)
from palamede_chessboard.players import registry

HUMAN, OTHER, AGENT = "human-player", "other-player", "agent-player"
#: 1.e4 Nf6 2.e5, black to move : the knight is attacked by the pawn.
BEFORE = "rnbqkb1r/pppppppp/5n2/4P3/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2"


def _exercise(kind: str, **solution) -> dict:
    return {
        "id": f"{kind}-1",
        "kind": kind,
        "source_game_id": "g1",
        "ply": 4,
        "fen": BEFORE,
        "side": "black",
        "motif": "ignored_threat",
        **solution,
        "state": "proposed",
        "attempts": [],
        "game_ids": [],
        "created_at": "2026-11-04T19:00:00+00:00",
    }


FIND = _exercise(
    "find_move",
    solution_uci="f6d5",
    solution_san="Nd5",
    gap_cp=250,
    slower_mates=["f6g4"],  # not a mate here : stands for one the engine saw
)
ATTACKED = _exercise("attacked", solution_squares=["f6"])


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid, kind in ((HUMAN, "human"), (OTHER, "human"), (AGENT, "agent")):
        registry.add_player(games_dir, pid, kind=kind)
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    add_exercises(games_dir, HUMAN, [dict(FIND), dict(ATTACKED)])
    return issued


def _auth(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _start(client: TestClient, token: str, exercise_id: str = "find_move-1"):
    return client.post(f"/api/coach/exercises/{exercise_id}/start", headers=_auth(token))


def _move(client: TestClient, token: str, game_id: str, uci: str):
    return client.post(f"/api/games/{game_id}/move", json={"uci": uci}, headers=_auth(token))


def _stored(games_dir: Path, exercise_id: str = "find_move-1") -> dict:
    return next(e for e in read_exercises(games_dir, HUMAN) if e["id"] == exercise_id)


# --- listing -------------------------------------------------------------------


def test_the_list_needs_a_token(client: TestClient, tokens: dict) -> None:
    assert client.get("/api/coach/exercises").status_code == 401


def test_the_list_is_the_tokens_own_without_solutions(client: TestClient, tokens: dict) -> None:
    body = client.get("/api/coach/exercises", headers=_auth(tokens[HUMAN])).json()
    assert body["player"] == HUMAN
    assert [e["id"] for e in body["exercises"]] == ["find_move-1", "attacked-1"]
    text = json.dumps(body)
    for field in play.SOLUTION_FIELDS:
        assert field not in text
    assert "f6d5" not in text and "Nd5" not in text
    other = client.get("/api/coach/exercises", headers=_auth(tokens[OTHER])).json()
    assert other == {"player": OTHER, "exercises": []}


def test_the_list_filters_by_state(client: TestClient, tokens: dict) -> None:
    url = "/api/coach/exercises?state=solved"
    assert client.get(url, headers=_auth(tokens[HUMAN])).json()["exercises"] == []
    bad = client.get("/api/coach/exercises?state=done", headers=_auth(tokens[HUMAN]))
    assert bad.status_code == 422


def test_an_unreadable_store_is_a_503(client: TestClient, tokens: dict, games_dir: Path) -> None:
    player_path(games_dir, HUMAN).write_text("{", encoding="utf-8")
    r = client.get("/api/coach/exercises", headers=_auth(tokens[HUMAN]))
    assert r.status_code == 503
    assert _start(client, tokens[HUMAN]).status_code == 503


def test_an_unreadable_registry_is_a_503(client: TestClient, tokens: dict, games_dir: Path) -> None:
    registry.registry_path(games_dir).write_text("{", encoding="utf-8")
    assert client.get("/api/coach/exercises", headers=_auth(tokens[HUMAN])).status_code == 503


# --- start ----------------------------------------------------------------------


def test_start_opens_an_unrated_game_from_the_position(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    r = _start(client, tokens[HUMAN])
    assert r.status_code == 201
    game_id = r.json()["game_id"]
    assert game_id.startswith(f"exo.{HUMAN}.")
    assert "solution_uci" not in r.json()["exercise"]
    state = GameState.load(game_id, games_dir=games_dir)
    assert state.board.fen() == BEFORE
    assert (state.metadata.white, state.metadata.black) == (play.OPPONENT, HUMAN)
    assert state.metadata.rated is False and state.metadata.result == "*"
    assert _stored(games_dir)["game_ids"] == [game_id]
    raw = client.get(f"/api/raw?game_id={game_id}").json()["pgn"]
    assert "Nd5" not in raw and "f6d5" not in raw


def test_the_opponent_is_no_account() -> None:
    with pytest.raises(registry.RegistryError):
        registry.player_id_of(play.OPPONENT)


def test_start_needs_a_token_and_an_own_exercise(client: TestClient, tokens: dict) -> None:
    assert client.post("/api/coach/exercises/find_move-1/start").status_code == 401
    assert _start(client, tokens[OTHER]).status_code == 404
    assert _start(client, tokens[HUMAN], "unknown").status_code == 404


def test_one_exercise_open_at_a_time(client: TestClient, tokens: dict, games_dir: Path) -> None:
    first = _start(client, tokens[HUMAN]).json()["game_id"]
    second = _start(client, tokens[HUMAN], "attacked-1").json()["game_id"]
    assert first != second
    closed = GameState.load(first, games_dir=games_dir).metadata
    assert closed.result == "1-0"  # the coach, white, wins an abandoned one
    assert closed.termination_note == play.ABANDONED_NOTE
    assert GameState.load(second, games_dir=games_dir).metadata.result == "*"
    stored = _stored(games_dir)
    assert stored["state"] == "proposed"  # an abandon is counted apart (Stéphane, #95)
    assert [(a["game_id"], a["abandoned"], a["ok"]) for a in stored["attempts"]] == [
        (first, True, False)
    ]
    assert _stored(games_dir, "attacked-1")["attempts"] == []


# --- the move -------------------------------------------------------------------


def test_a_wrong_move_is_refused_and_not_played(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    r = _move(client, tokens[HUMAN], game_id, "h7h6")
    assert r.status_code == 409
    assert r.json() == {"detail": play.NOT_THE_MOVE}
    state = GameState.load(game_id, games_dir=games_dir)
    assert state.board.fen() == BEFORE and state.metadata.result == "*"
    stored = _stored(games_dir)
    assert stored["state"] == "failed"
    assert [(a["move"], a["ok"]) for a in stored["attempts"]] == [("h7h6", False)]


def test_a_slower_mate_is_told_there_is_faster(client: TestClient, tokens: dict) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    r = _move(client, tokens[HUMAN], game_id, "f6g4")
    assert r.status_code == 409 and r.json()["detail"] == play.FASTER_MATE


def test_the_right_move_wins_the_game(client: TestClient, tokens: dict, games_dir: Path) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    r = _move(client, tokens[HUMAN], game_id, "f6d5")
    assert r.status_code == 200
    assert r.json()["move"]["san"] == "Nd5"
    meta = GameState.load(game_id, games_dir=games_dir).metadata
    assert meta.result == "0-1" and meta.termination_note == play.SOLVED_NOTE
    assert _stored(games_dir)["state"] == "solved"
    # Solved : now the player may read the solution.
    body = client.get("/api/coach/exercises?state=solved", headers=_auth(tokens[HUMAN])).json()
    assert body["exercises"][0]["solution_san"] == "Nd5"
    assert _move(client, tokens[HUMAN], game_id, "d7d6").status_code in (403, 409)


def test_solved_after_a_wrong_try_stays_failed_until_a_clean_game(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    _move(client, tokens[HUMAN], game_id, "h7h6")
    assert _move(client, tokens[HUMAN], game_id, "f6d5").status_code == 200
    assert _stored(games_dir)["state"] == "failed"
    again = _start(client, tokens[HUMAN]).json()["game_id"]
    assert _move(client, tokens[HUMAN], again, "f6d5").status_code == 200
    assert _stored(games_dir)["state"] == "solved"


def test_another_player_cannot_play_it(client: TestClient, tokens: dict, games_dir: Path) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    assert _move(client, tokens[OTHER], game_id, "f6d5").status_code == 403
    assert _move(client, None, game_id, "f6d5").status_code == 401
    assert _stored(games_dir)["attempts"] == []


def test_an_attacked_exercise_takes_no_move(client: TestClient, tokens: dict) -> None:
    game_id = _start(client, tokens[HUMAN], "attacked-1").json()["game_id"]
    r = _move(client, tokens[HUMAN], game_id, "f6d5")
    assert r.status_code == 409 and "cases" in r.json()["detail"]


def test_an_illegal_move_is_still_a_422(client: TestClient, tokens: dict) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    r = client.post(f"/api/games/{game_id}/move", json={"san": "Ke2"}, headers=_auth(tokens[HUMAN]))
    assert r.status_code == 422


def test_the_player_may_resign_an_exercise(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    r = client.post(f"/api/games/{game_id}/resign", headers=_auth(tokens[HUMAN]))
    assert r.status_code == 200
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "1-0"
    # Resigned, still black to move on the board : the solution is no longer judged.
    r = _move(client, tokens[HUMAN], game_id, "f6d5")
    assert r.status_code == 409 and "terminé" in r.json()["detail"]
    assert GameState.load(game_id, games_dir=games_dir).san_history == []
    stored = _stored(games_dir)
    assert stored["state"] == "proposed"
    assert [(a["game_id"], a["abandoned"], a["ok"]) for a in stored["attempts"]] == [
        (game_id, True, False)
    ]


def test_an_abandon_leaves_a_failed_exercise_failed(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    _move(client, tokens[HUMAN], game_id, "d7d6")
    client.post(f"/api/games/{game_id}/resign", headers=_auth(tokens[HUMAN]))
    stored = _stored(games_dir)
    assert stored["state"] == "failed"
    assert [a.get("abandoned", False) for a in stored["attempts"]] == [False, True]


def test_only_the_player_records_an_abandon(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    for token in (tokens[AGENT], tokens[OTHER], None):
        r = client.post(f"/api/games/{game_id}/resign", headers=_auth(token))
        assert r.status_code in (401, 403)
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "*"
    assert _stored(games_dir)["attempts"] == []


def test_a_resign_goes_through_an_unreadable_store(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    player_path(games_dir, HUMAN).write_text("{", encoding="utf-8")
    r = client.post(f"/api/games/{game_id}/resign", headers=_auth(tokens[HUMAN]))
    assert r.status_code == 200
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "1-0"


def test_an_unreadable_store_refuses_the_move(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    player_path(games_dir, HUMAN).write_text("{", encoding="utf-8")
    r = _move(client, tokens[HUMAN], game_id, "f6d5")
    assert r.status_code == 503
    assert GameState.load(game_id, games_dir=games_dir).san_history == []


# --- the answer -----------------------------------------------------------------


def _answer(client: TestClient, token: str, squares: list[str], exercise_id: str = "attacked-1"):
    return client.post(
        f"/api/coach/exercises/{exercise_id}/answer",
        json={"squares": squares},
        headers=_auth(token),
    )


def test_an_answer_needs_a_started_exercise(client: TestClient, tokens: dict) -> None:
    assert _answer(client, tokens[HUMAN], ["f6"]).status_code == 409


def test_answers_wrong_then_right(client: TestClient, tokens: dict, games_dir: Path) -> None:
    game_id = _start(client, tokens[HUMAN], "attacked-1").json()["game_id"]
    wrong = _answer(client, tokens[HUMAN], ["f6", "e7"])
    assert wrong.status_code == 409
    assert GameState.load(game_id, games_dir=games_dir).metadata.result == "*"
    right = _answer(client, tokens[HUMAN], [" F6 "])
    assert right.status_code == 200
    assert right.json()["exercise"]["state"] == "failed"  # one wrong try in that game
    # Not solved cleanly : the solution stays hidden.
    assert "solution_squares" not in right.json()["exercise"]
    meta = GameState.load(game_id, games_dir=games_dir).metadata
    assert meta.result == "0-1" and meta.termination_note == play.SOLVED_NOTE
    assert _answer(client, tokens[HUMAN], ["f6"]).status_code == 409  # over


def test_an_answer_is_squares(client: TestClient, tokens: dict) -> None:
    _start(client, tokens[HUMAN], "attacked-1")
    assert _answer(client, tokens[HUMAN], ["z9"]).status_code == 422
    assert _answer(client, tokens[HUMAN], ["f6"], "find_move-1").status_code == 409
    assert _answer(client, tokens[OTHER], ["f6"]).status_code == 404


# --- reports --------------------------------------------------------------------


@pytest.fixture
def reported(games_dir: Path, tokens: dict) -> str:
    GameState.create(
        GameMetadata(
            game_id="g1",
            white="A Grandmaster",
            black="Human-Player",  # compared normalised (Admin, #95)
            arbiter="Agent-Player",  # the arbiter only, not a player
            time_control=TimeControl(format="unrated"),
        ),
        games_dir=games_dir,
    )
    write_report(games_dir, "g1", {"game_id": "g1", "sides": {}})
    return "g1"


def test_a_report_for_players_and_arbiter_only(
    client: TestClient, tokens: dict, reported: str
) -> None:
    url = f"/api/coach/reports/{reported}"
    assert client.get(url, headers=_auth(tokens[HUMAN])).json() == {"game_id": "g1", "sides": {}}
    assert client.get(url, headers=_auth(tokens[AGENT])).status_code == 200
    assert client.get(url, headers=_auth(tokens[OTHER])).status_code == 403
    assert client.get(url).status_code == 401


def test_an_arbiter_no_account_carries_gives_no_access(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    GameState.create(
        GameMetadata(
            game_id="g2",
            white="Somebody Else",
            black="A Grandmaster",
            arbiter="other player",  # not an id : never read as other-player
        ),
        games_dir=games_dir,
    )
    write_report(games_dir, "g2", {"game_id": "g2"})
    assert client.get("/api/coach/reports/g2", headers=_auth(tokens[OTHER])).status_code == 403


def test_reports_missing_unknown_or_malformed(
    client: TestClient, tokens: dict, games_dir: Path, reported: str
) -> None:
    headers = _auth(tokens[HUMAN])
    assert client.get("/api/coach/reports/nope", headers=headers).status_code == 404
    GameState.create(GameMetadata(game_id="g3", white=HUMAN, black=AGENT), games_dir=games_dir)
    r = client.get("/api/coach/reports/g3", headers=headers)
    assert r.status_code == 404 and "rapport" in r.json()["detail"]
    assert client.get("/api/coach/reports/.hidden", headers=headers).status_code == 400


def test_the_starting_position_is_legal_for_the_side() -> None:
    board = chess.Board(BEFORE)
    assert board.turn == chess.BLACK
    assert chess.Move.from_uci("f6d5") in board.legal_moves
    assert chess.Move.from_uci("f6g4") in board.legal_moves


# --- the progress (#95, step 2) ---------------------------------------------------


def test_the_progress_needs_a_token_the_same_401(client: TestClient, tokens: dict) -> None:
    missing = client.get("/api/coach/progress")
    wrong = client.get("/api/coach/progress", headers=_auth("not-a-token"))
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()


def test_the_progress_is_the_tokens_own_without_solutions(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    write_report(
        games_dir,
        "duel",
        {
            "game_id": "duel",
            "game_date": "2026-11-05",
            "game_date_source": "header",
            "sides": {
                "white": {"player": HUMAN, "plies": 20, "by_motif": {"hanging_piece": 1}},
                "black": {"player": OTHER, "plies": 20, "by_motif": {"missed_mate": 3}},
            },
        },
    )
    r = client.get("/api/coach/progress", headers=_auth(tokens[HUMAN]))
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    body = r.json()
    assert body["player"] == HUMAN
    assert body["games"]["recent"]["motifs"]["hanging_piece"]["mistakes"] == 1
    assert body["games"]["recent"]["motifs"]["missed_mate"]["mistakes"] == 0
    assert body["suggestion"] == {
        "id": "find_move-1",
        "motif": "ignored_threat",
        "kind": "find_move",
    }
    text = json.dumps(body)
    for field in play.SOLUTION_FIELDS:
        assert field not in text
    assert "f6d5" not in text and BEFORE not in text
    other = client.get("/api/coach/progress", headers=_auth(tokens[OTHER])).json()
    assert other["player"] == OTHER and other["exercises"] == {} and other["suggestion"] is None
    assert other["games"]["recent"]["motifs"]["missed_mate"]["mistakes"] == 3


def test_an_abandon_shows_in_the_progress(client: TestClient, tokens: dict) -> None:
    game_id = _start(client, tokens[HUMAN]).json()["game_id"]
    client.post(f"/api/games/{game_id}/resign", headers=_auth(tokens[HUMAN]))
    counts = client.get("/api/coach/progress", headers=_auth(tokens[HUMAN])).json()["exercises"]
    assert counts["ignored_threat"]["abandoned_games"] == 1
    assert counts["ignored_threat"]["first_try"] == {"tried": 0, "ok": 0}


def test_an_unreadable_store_is_a_503_for_the_progress(
    client: TestClient, tokens: dict, games_dir: Path
) -> None:
    player_path(games_dir, HUMAN).write_text("{", encoding="utf-8")
    assert client.get("/api/coach/progress", headers=_auth(tokens[HUMAN])).status_code == 503
