"""« Mes parties » in the game selector (#245, rest of #46 point 1).

Tested as text, like the rest of the viewer : with a token whose player
``/api/me`` confirmed, the list asks ``player=`` by default ; without one
the box stays hidden and the query is unchanged. The server side of
``player=`` is checked through the endpoint.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState
from palamede_chessboard.viewer.models import TEMPLATES_DIR
from tests.viewer.conftest import _page_and_scripts

GAMES_JS = (TEMPLATES_DIR / "viewer" / "_games_list.js.jinja").read_text(encoding="utf-8")


def _query_body() -> str:
    body = GAMES_JS[GAMES_JS.index("function gselQuery(limit, offset)") :]
    return body[: body.index("\n}\n")]


def test_the_box_is_hidden_and_ticked_by_default(client: TestClient) -> None:
    page = _page_and_scripts(client)
    assert (
        '<label class="gsel-opt" id="gsel-mine-opt" hidden>'
        '<input type="checkbox" id="gsel-mine" checked /> mes parties</label>'
    ) in page


def test_player_only_for_a_confirmed_token() -> None:
    body = _query_body()
    # A token still checking, refused or unknown is not a player.
    assert "const me = KEY_WHO.state === 'ok' ? KEY_WHO.player : '';" in body
    assert "gselMineOpt.hidden = !me;" in body
    assert "if (me && gselMine.checked) p.set('player', me);" in body
    assert body.count("'player'") == 1


def test_the_player_is_sent_by_both_list_reads() -> None:
    # The panel load and the 30 s refresh of « En cours » share gselQuery.
    assert GAMES_JS.count("await gselQuery(") == 2
    assert "fetch('/api/games?" in _query_body()
    assert GAMES_JS.count("fetch('/api/games") == 1


def test_unticking_reloads_the_list() -> None:
    assert "gselMine.addEventListener('change', () => gselLoad(false));" in GAMES_JS


def _game(games_dir: Path, game_id: str, white: str, black: str) -> None:
    GameState.create(
        GameMetadata(game_id=game_id, white=white, black=black), games_dir=games_dir
    ).save()


def test_the_endpoint_keeps_only_the_player_s_games(client: TestClient, games_dir: Path) -> None:
    _game(games_dir, "mine-white", "stephane", "leader")
    _game(games_dir, "mine-black", "admin", "stephane")
    _game(games_dir, "other", "leader", "junior")
    every = client.get("/api/games").json()["in_progress"]
    mine = client.get("/api/games?player=stephane").json()["in_progress"]
    assert {g["game_id"] for g in every} == {"mine-white", "mine-black", "other"}
    assert {g["game_id"] for g in mine} == {"mine-white", "mine-black"}
