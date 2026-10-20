"""Viewer game selector (#46) : filters, paging and ETag of /api/games."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState
from palamede_chessboard.viewer.games_query import etag_matches, select_games


def _entry(game_id: str, result: str = "1-0", rated: bool = True, **kw: str) -> dict:
    return {
        "game_id": game_id,
        "white": kw.get("white", "leader"),
        "black": kw.get("black", "junior"),
        "result": result,
        "rated": rated,
    }


# ---- select_games (pure) ---------------------------------------------------


def test_in_progress_is_never_paged_nor_hidden() -> None:
    entries = [_entry(f"live-{i}", "*", rated=False) for i in range(30)] + [_entry("done")]
    out = select_games(entries, exercises=False, limit=1)
    assert len(out["in_progress"]) == 30
    assert [g["game_id"] for g in out["completed"]] == ["done"]


def test_completed_paging() -> None:
    entries = [_entry(f"g{i:02}") for i in range(45)]
    first = select_games(entries, limit=20)
    assert [g["game_id"] for g in first["completed"]][:2] == ["g00", "g01"]
    assert (first["completed_total"], first["has_more"]) == (45, True)
    last = select_games(entries, limit=20, offset=40)
    assert [g["game_id"] for g in last["completed"]] == [f"g{i}" for i in range(40, 45)]
    assert last["has_more"] is False
    assert select_games(entries, limit=0)["completed"] == []


def test_exercises_false_hides_finished_unrated_only() -> None:
    entries = [
        _entry("exo-1", rated=False),
        _entry("classee"),
        _entry("exo-live", "*", rated=False),
    ]
    out = select_games(entries, exercises=False)
    assert [g["game_id"] for g in out["completed"]] == ["classee"]
    assert [g["game_id"] for g in out["in_progress"]] == ["exo-live"]
    assert out["completed_total"] == 1


def test_text_search_on_id_and_players() -> None:
    entries = [
        _entry("exo-stephane-001", white="stephane"),
        _entry("a", black="Admin"),
        _entry("b"),
    ]
    assert [g["game_id"] for g in select_games(entries, q="EXO")["completed"]] == [
        "exo-stephane-001"
    ]
    assert [g["game_id"] for g in select_games(entries, q="admin")["completed"]] == ["a"]


@pytest.mark.parametrize("who", ["leader", "Leader", "devleader"])
def test_player_filter_normalises_identity(who: str) -> None:
    entries = [_entry("x", white="Leader"), _entry("y", white="admin", black="junior")]
    assert [g["game_id"] for g in select_games(entries, player=who)["completed"]] == ["x"]


# ---- /api/games ---------------------------------------------------------


def _game(games_dir: Path, game_id: str, *, result: str = "*", rated: bool = True) -> GameState:
    state = GameState.create(
        GameMetadata(game_id=game_id, white="leader", black="junior", rated=rated),
        games_dir=games_dir,
    )
    state.metadata.result = result
    state.save()
    return state


def test_endpoint_defaults_to_twenty_finished(client: TestClient, games_dir: Path) -> None:
    for i in range(25):
        _game(games_dir, f"done-{i:02}", result="1-0")
    body = client.get("/api/games").json()
    assert len(body["completed"]) == 20
    assert (body["completed_total"], body["has_more"]) == (25, True)
    entry = body["completed"][0]
    assert entry["rated"] is True and entry["date"]


def test_endpoint_filters(client: TestClient, games_dir: Path) -> None:
    _game(games_dir, "exo-1", result="1-0", rated=False)
    _game(games_dir, "partie-1", result="0-1")
    body = client.get("/api/games?exercises=false&q=1").json()
    assert [g["game_id"] for g in body["completed"]] == ["partie-1"]


@pytest.mark.parametrize("query", ["limit=101", "limit=-1", "offset=-1", "q=" + "x" * 65])
def test_endpoint_rejects_bad_params(client: TestClient, games_dir: Path, query: str) -> None:
    assert client.get("/api/games?" + query).status_code == 422


def test_etag_304_until_a_pgn_changes(client: TestClient, games_dir: Path) -> None:
    state = _game(games_dir, "live")
    first = client.get("/api/games")
    etag = first.headers["ETag"]
    assert client.get("/api/games", headers={"If-None-Match": etag}).status_code == 304
    # a different query is a different listing
    other = client.get("/api/games?limit=5", headers={"If-None-Match": etag})
    assert other.status_code == 200
    state.play_move("e4")
    pgn = games_dir / "live.pgn"
    os.utime(pgn, ns=(pgn.stat().st_atime_ns, pgn.stat().st_mtime_ns + 1_000_000))
    after = client.get("/api/games", headers={"If-None-Match": etag})
    assert after.status_code == 200
    assert after.json()["in_progress"][0]["ply"] == 1


# ---- ETag faible derrière un reverse proxy (revue Admin #52) ----------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ('"abc"', True),
        ('W/"abc"', True),
        ('"zzz", W/"abc"', True),
        ("*", True),
        ('"zzz"', False),
        ("", False),
        (None, False),
    ],
)
def test_etag_matches(header: str | None, expected: bool) -> None:
    assert etag_matches(header, '"abc"') is expected


@pytest.mark.parametrize("path", ["/api/games", "/api/positions?game_id=live"])
def test_weak_etag_from_proxy_still_gets_304(
    client: TestClient, games_dir: Path, path: str
) -> None:
    _game(games_dir, "live")
    etag = client.get(path).headers["ETag"]
    assert client.get(path, headers={"If-None-Match": "W/" + etag}).status_code == 304
