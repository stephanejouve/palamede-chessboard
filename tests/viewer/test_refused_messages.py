"""A refused move or action, in French and French notation, above the board (#322)."""

from __future__ import annotations

import re
from pathlib import Path

import chess
import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.players import registry
from palamede_chessboard.viewer import refusals
from palamede_chessboard.viewer.models import TEMPLATES_DIR

BOARD_JS = (TEMPLATES_DIR / "viewer" / "_board.js.jinja").read_text(encoding="utf-8")
GAME = "refusals"
#: White to move and mated (back-rank mate) : no legal move, result not set.
MATED_WHITE_TO_MOVE = "6k1/8/8/8/8/8/5PPP/r5K1 w - - 0 1"


@pytest.fixture
def tokens(games_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    issued = {}
    for pid in ("alice", "bob"):
        registry.add_player(games_dir, pid, kind="human")
        issued[pid] = registry.issue_token(games_dir, pid, label="tests")
    GameState.create(
        GameMetadata(
            game_id=GAME, white="alice", black="bob", time_control=TimeControl(format="unrated")
        ),
        games_dir=games_dir,
    )
    return issued


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _move(client: TestClient, token: str, **body: str):
    return client.post(f"/api/games/{GAME}/move", json=body, headers=_bearer(token))


class TestWording:
    @pytest.mark.parametrize(
        ("san", "french"),
        [("Rf1", "Tf1"), ("Nf3", "Cf3"), ("Bb5+", "Fb5+"), ("Qxd8#", "Dxd8#"), ("Kg1", "Rg1")],
    )
    def test_pieces_are_said_the_french_way(self, san: str, french: str) -> None:
        assert refusals.french_san(san) == french

    def test_pawns_files_and_castling_are_unchanged(self) -> None:
        assert refusals.french_san("exd8=Q+") == "exd8=D+"
        assert refusals.french_san("O-O-O") == "O-O-O"
        assert refusals.french_san("bxc6") == "bxc6"

    def test_an_illegal_attempt_is_the_piece_and_its_target(self) -> None:
        board = chess.Board()
        assert refusals.attempted(board, chess.Move.from_uci("h1f1")) == "Tf1"
        assert refusals.attempted(board, chess.Move.from_uci("e2e5")) == "e5"
        assert refusals.attempted(board, chess.Move.from_uci("e4e5")) == "aucune pièce en e4"

    def test_the_sentence(self) -> None:
        assert refusals.not_possible("Tf1") == (
            "Coup refusé : Tf1 n'est pas possible dans cette position."
        )
        assert refusals.not_possible("aucune pièce en e4") == "Coup refusé : aucune pièce en e4."


class TestMoveRefusals:
    def test_the_example_of_the_issue(self, client: TestClient, tokens: dict) -> None:
        r = _move(client, tokens["alice"], uci="h1f1")
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail == "Coup refusé : Tf1 n'est pas possible dans cette position."
        assert "UCI" not in detail and "ply" not in detail

    def test_an_unreadable_move(self, client: TestClient, tokens: dict) -> None:
        r = _move(client, tokens["alice"], uci="zz")
        assert (r.status_code, r.json()["detail"]) == (422, refusals.UNREADABLE)

    def test_an_illegal_san_is_said_in_french(self, client: TestClient, tokens: dict) -> None:
        r = _move(client, tokens["alice"], san="Rf1")
        assert r.status_code == 422
        assert r.json()["detail"] == "Coup refusé : Tf1 n'est pas possible dans cette position."

    def test_no_move_at_all(self, client: TestClient, tokens: dict) -> None:
        r = _move(client, tokens["alice"])
        assert (r.status_code, r.json()["detail"]) == (400, refusals.NO_MOVE)

    def test_a_move_on_a_finished_game(
        self, client: TestClient, tokens: dict, games_dir: Path
    ) -> None:
        GameState.load(GAME, games_dir=games_dir).resign(by="black")
        r = _move(client, tokens["alice"], uci="e2e4")
        assert (r.status_code, r.json()["detail"]) == (409, refusals.GAME_OVER)

    @pytest.mark.parametrize("body", [{"san": "Kh1"}, {"uci": "g1h1"}])
    def test_a_game_set_up_from_a_mated_position(
        self, client: TestClient, tokens: dict, games_dir: Path, body: dict
    ) -> None:
        # Result still « * », but the board is over : the board decides (review Junior #326).
        GameState.create(
            GameMetadata(
                game_id="mated",
                white="alice",
                black="bob",
                time_control=TimeControl(format="unrated"),
            ),
            games_dir=games_dir,
            starting_fen=MATED_WHITE_TO_MOVE,
        )
        r = client.post("/api/games/mated/move", json=body, headers=_bearer(tokens["alice"]))
        assert (r.status_code, r.json()["detail"]) == (409, refusals.GAME_OVER)

    def test_the_code_goes_to_a_header_never_to_the_screen(
        self, client: TestClient, tokens: dict
    ) -> None:
        r = _move(client, tokens["bob"], uci="e7e5")  # White to move
        assert r.status_code == 403
        assert r.json()["detail"] == "Ce n'est pas votre trait"
        assert r.headers["x-refusal-code"] == "NOT_YOUR_TURN"

    def test_a_missing_token_keeps_its_bearer_challenge(
        self, client: TestClient, tokens: dict
    ) -> None:
        r = client.post(f"/api/games/{GAME}/move", json={"uci": "e2e4"})
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"
        assert r.headers["x-refusal-code"] == "UNAUTHENTICATED"
        assert "(" not in r.json()["detail"].replace("(bouton 🔑)", "")


class TestEndRefusals:
    def test_resigning_a_finished_game(self, client: TestClient, tokens: dict) -> None:
        client.post(f"/api/games/{GAME}/resign", headers=_bearer(tokens["alice"]))
        r = client.post(f"/api/games/{GAME}/resign", headers=_bearer(tokens["bob"]))
        assert (r.status_code, r.json()["detail"]) == (409, "La partie est terminée.")

    def test_accepting_without_an_offer(self, client: TestClient, tokens: dict) -> None:
        r = client.post(f"/api/games/{GAME}/draw/accept", headers=_bearer(tokens["bob"]))
        assert (r.status_code, r.json()["detail"]) == (409, "Aucune proposition de nulle en cours.")

    def test_accepting_one_s_own_offer(self, client: TestClient, tokens: dict) -> None:
        client.post(f"/api/games/{GAME}/draw/offer", headers=_bearer(tokens["alice"]))
        r = client.post(f"/api/games/{GAME}/draw/accept", headers=_bearer(tokens["alice"]))
        assert r.status_code == 409
        assert (
            r.json()["detail"] == "C'est votre propre proposition : votre adversaire doit répondre."
        )

    def test_offering_while_the_opponent_s_offer_stands(
        self, client: TestClient, tokens: dict
    ) -> None:
        client.post(f"/api/games/{GAME}/draw/offer", headers=_bearer(tokens["alice"]))
        r = client.post(f"/api/games/{GAME}/draw/offer", headers=_bearer(tokens["bob"]))
        assert r.status_code == 409
        assert r.json()["detail"].startswith("Votre adversaire propose déjà la nulle")


class TestPage:
    def test_the_banner_is_above_the_board_not_inside(self, client: TestClient) -> None:
        page = client.get("/").text
        banner = '<div class="move-error" id="move-error" role="alert" hidden></div>'
        assert banner in page
        assert '<div id="board"></div>' in page
        assert page.index(banner) < page.index('<div id="board"></div>')

    def test_the_script_leaves_the_banner_above_the_board(self) -> None:
        """#332 smoke : moved back into #board after chessground, the banner was
        painted under cg-container (absolute, 100 %) : every refusal unseen."""
        # The banner is reached once, by its id, and only shown or hidden then :
        # no script holds it any other way, so none can move it (allowlist).
        scripts = "".join(
            path.read_text(encoding="utf-8")
            for path in sorted((TEMPLATES_DIR / "viewer").glob("*.js.jinja"))
        )
        assert scripts.count("move-error") == 1
        assert "const errorEl = document.getElementById('move-error');" in BOARD_JS
        uses = re.findall(r"[\w.(!]*errorEl\b[^;)]*", BOARD_JS)
        allowed = {
            "errorEl = document.getElementById('move-error'",
            "(!errorEl",
            "(errorEl",
            "errorEl.textContent = msg",
            "errorEl.hidden = false",
            "errorEl.hidden = true",
        }
        assert set(uses) <= allowed, set(uses) - allowed

    def test_the_banner_is_in_the_flow_at_a_normal_size(self) -> None:
        css = (TEMPLATES_DIR / "viewer" / "_styles.css.jinja").read_text(encoding="utf-8")
        rule = next(line for line in css.splitlines() if line.strip().startswith(".move-error {"))
        assert "position: absolute" not in rule and "font-size: 15px" in rule

    def test_the_script_never_shows_a_bare_http_code(self) -> None:
        assert "'HTTP ' + r.status" not in BOARD_JS
        assert "Network error" not in BOARD_JS
        assert "Serveur injoignable : coup non envoyé." in BOARD_JS
        assert "errorEl.hidden = false;" in BOARD_JS and "errorEl.hidden = true;" in BOARD_JS
