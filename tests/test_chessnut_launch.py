"""A launch without ``--game`` nor ``--color`` (#90) : the token says the rest."""

from __future__ import annotations

import chess
import pytest
from palamede_chessnut import launch, viewer
from palamede_chessnut.cli import bridge_from_args

TOKEN = "secret-token-never-shown"
VIEWER = "https://viewer.test"
BASE = ["--viewer", VIEWER]
LISTING = {
    "in_progress": [
        {"game_id": "009", "white": "cd", "black": "stephane"},
        {"game_id": "008d", "white": "stephane", "black": "relais"},
    ],
    "completed": [],
}


class FakeViewer:
    """Answers of the viewer by path ; records what was asked, and with which token."""

    def __init__(self, monkeypatch) -> None:
        self.me: tuple[int | None, object] = (200, {"player": "stephane", "kind": "human"})
        self.seats: dict[str, tuple[int | None, object]] = {
            "009": (200, {"game_id": "009", "player": "stephane", "colors": ["black"]}),
            "008d": (200, {"game_id": "008d", "player": "stephane", "colors": ["white"]}),
        }
        self.listing: object = LISTING
        self.asked: list[tuple[str, str | None]] = []
        monkeypatch.setattr(viewer, "get_with_token", self.get_with_token)
        monkeypatch.setattr(viewer, "get_json", self.get_json)
        monkeypatch.setenv("PALAMEDE_PLAYER_TOKEN", TOKEN)

    def get_with_token(self, url: str, token: str):
        path = url.removeprefix(VIEWER)
        self.asked.append((path, token))
        if path == "/api/me":
            return self.me
        game = path.removeprefix("/api/games/").removesuffix("/seat")
        return self.seats.get(game, (404, None))

    def get_json(self, url: str):
        self.asked.append((url.removeprefix(VIEWER), None))
        if isinstance(self.listing, Exception):
            raise self.listing
        return self.listing

    def paths(self) -> list[str]:
        return [path for path, _ in self.asked]


@pytest.fixture
def fake(monkeypatch) -> FakeViewer:
    return FakeViewer(monkeypatch)


def test_no_game_no_color_newest_game_and_its_side(fake) -> None:
    bridge = bridge_from_args(BASE, "t")
    assert (bridge.game_id, bridge.player) == ("009", chess.BLACK)
    assert fake.paths() == ["/api/me", "/api/games?player=stephane", "/api/games/009/seat"]


def test_the_token_goes_only_to_the_authenticated_routes(fake) -> None:
    bridge_from_args(BASE, "t")
    assert [token for _, token in fake.asked] == [TOKEN, None, TOKEN]


def test_given_game_skips_me_and_the_listing(fake) -> None:
    bridge = bridge_from_args([*BASE, "--game", "008d"], "t")
    assert (bridge.game_id, bridge.player) == ("008d", chess.WHITE)
    assert fake.paths() == ["/api/games/008d/seat"]


def test_given_game_and_color_ask_nothing(fake) -> None:
    bridge = bridge_from_args([*BASE, "--game", "008d", "--color", "black"], "t")
    assert (bridge.game_id, bridge.player) == ("008d", chess.BLACK)
    assert fake.asked == []


def test_given_color_wins_over_the_seat(fake) -> None:
    bridge = bridge_from_args([*BASE, "--color", "white"], "t")
    assert (bridge.game_id, bridge.player) == ("009", chess.WHITE)
    assert "/api/games/009/seat" not in fake.paths()


def test_a_game_against_oneself_is_played_as_white(fake) -> None:
    fake.seats["009"] = (200, {"colors": ["white", "black"]})
    assert bridge_from_args(BASE, "t").player == chess.WHITE


@pytest.mark.parametrize("status", [401, 403])
def test_refused_token_exits_before_any_listing(fake, status) -> None:
    fake.me = (status, None)
    with pytest.raises(SystemExit) as refused:
        bridge_from_args(BASE, "t")
    assert "refuses the player token" in str(refused.value)
    assert fake.paths() == ["/api/me"]


def test_old_viewer_without_me_says_to_give_the_game(fake) -> None:
    fake.me = (404, None)
    with pytest.raises(SystemExit) as refused:
        bridge_from_args(BASE, "t")
    assert "--game" in str(refused.value)


@pytest.mark.parametrize(
    "answer",
    [(None, None), (200, {"kind": "human"}), (200, {"player": ""}), (200, ["stephane"])],
    ids=["injoignable", "sans player", "player vide", "pas un objet"],
)
def test_unusable_me_exits(fake, answer) -> None:
    fake.me = answer
    with pytest.raises(SystemExit):
        bridge_from_args(BASE, "t")
    assert fake.paths() == ["/api/me"]


def test_no_game_in_progress_exits(fake) -> None:
    fake.listing = {"in_progress": [], "completed": []}
    with pytest.raises(SystemExit) as refused:
        bridge_from_args(BASE, "t")
    assert "no game in progress for stephane" in str(refused.value)


def test_unreadable_listing_exits(fake) -> None:
    fake.listing = OSError("down")
    with pytest.raises(SystemExit) as refused:
        bridge_from_args(BASE, "t")
    assert "does not answer" in str(refused.value)


def test_a_name_the_viewer_could_not_give_is_skipped(fake) -> None:
    fake.listing = {
        "in_progress": [
            {"game_id": "../etc", "white": "stephane", "black": "x"},
            {"game_id": "", "white": "stephane", "black": "x"},
            {"game_id": "008d", "white": "stephane", "black": "relais"},
        ]
    }
    assert bridge_from_args(BASE, "t").game_id == "008d"


@pytest.mark.parametrize(
    ("seat", "said"),
    [
        ((403, None), "does not play"),
        ((404, None), "no such game"),
        ((401, None), "refuses the player token"),
        ((None, None), "does not answer"),
        ((200, {"colors": []}), "no side for you"),
    ],
    ids=["pas joueur", "inconnue", "jeton refusé", "injoignable", "aucun côté"],
)
def test_seat_refusals_exit_without_a_bridge(fake, seat, said) -> None:
    fake.seats["009"] = seat
    with pytest.raises(SystemExit) as refused:
        bridge_from_args(BASE, "t")
    assert said in str(refused.value)


def test_the_token_is_never_in_an_exit_message(fake) -> None:
    for me in ((401, None), (404, None), (None, None), (200, {})):
        fake.me = me
        with pytest.raises(SystemExit) as refused:
            bridge_from_args(BASE, "t")
        assert TOKEN not in str(refused.value)


def test_account_of_token_alone(fake) -> None:
    assert launch.account_of_token(VIEWER, TOKEN) == "stephane"
