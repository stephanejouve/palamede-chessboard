"""The Maia bot (#93, PR 2) : which games it plays, and how.

lc0 and the viewer are replaced by fakes : the rules are what is tested
here, the real engine is checked by the image's smoke test.
"""

from __future__ import annotations

import logging
from pathlib import Path

import chess
import pytest
from palamede_maia import bot as bot_module
from palamede_maia import viewer
from palamede_maia.bot import Bot, level_of
from palamede_maia.cli import main, parse_humans, read_tokens

START = chess.STARTING_FEN
TOKENS = {"bot-maia-1500": "tok-1500", "bot-maia-1900": "tok-1900"}


def _game(game_id="g1", white="stephane", black="bot-maia-1500", turn="black", **extra):
    return {
        "game_id": game_id,
        "white": white,
        "black": black,
        "turn": turn,
        "result": "*",
        "is_game_over": False,
        "rated": False,
        **extra,
    }


class FakeViewer:
    def __init__(self, monkeypatch, games, status=200) -> None:
        self.games = games
        self.status = status
        self.played: list[tuple[str, str, str]] = []
        monkeypatch.setattr(viewer, "games_in_progress", lambda url: self.games)
        monkeypatch.setattr(viewer, "last_fen", lambda url, game_id: START)
        monkeypatch.setattr(viewer, "play", self.play)

    def play(self, url, game_id, uci, token):
        self.played.append((game_id, uci, token))
        return self.status


def _bot(humans=("stephane", "cilou"), move="e7e5"):
    asked = []

    def move_of(elo, fen):
        asked.append((elo, fen))
        return move

    return Bot("http://viewer", dict(TOKENS), frozenset(humans), move_of), asked


def test_plays_its_move_with_its_own_token(monkeypatch) -> None:
    fake = FakeViewer(monkeypatch, [_game()])
    bot, asked = _bot()
    assert bot.play_round() == 1
    assert asked == [(1500, START)]
    assert fake.played == [("g1", "e7e5", "tok-1500")]


def test_level_follows_the_account(monkeypatch) -> None:
    FakeViewer(monkeypatch, [_game(black="bot-maia-1900")])
    bot, asked = _bot()
    bot.play_round()
    assert asked[0][0] == 1900


@pytest.mark.parametrize(
    "game",
    [
        _game(turn="white"),
        _game(is_game_over=True),
        _game(result="0-1"),
        _game(black="bot-maia-1300"),
        _game(black="leader", white="stephane"),
    ],
    ids=["pas son trait", "finie", "résultat posé", "jeton absent", "pas un bot"],
)
def test_nothing_to_play(monkeypatch, game) -> None:
    fake = FakeViewer(monkeypatch, [game])
    bot, asked = _bot()
    assert bot.play_round() == 0
    assert asked == [] and fake.played == []


@pytest.mark.parametrize("rated", [True, None, "false"], ids=["classée", "absent", "chaîne"])
def test_never_plays_a_rated_game(monkeypatch, caplog, rated) -> None:
    """Fair-play : only a game flagged unrated by the server, fail-closed."""
    fake = FakeViewer(monkeypatch, [_game(rated=rated)])
    bot, asked = _bot()
    with caplog.at_level(logging.INFO):
        bot.play_round()
        bot.play_round()
    assert fake.played == [] and asked == []
    assert sum("rated game" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.parametrize("opponent", ["leader", "junior", "bot-maia-1900", "inconnu"])
def test_never_plays_an_agent_a_bot_or_a_stranger(monkeypatch, opponent) -> None:
    """Leader #93 : Maia plays humans, never for nor against an agent."""
    fake = FakeViewer(monkeypatch, [_game(white=opponent)])
    bot, asked = _bot()
    assert bot.play_round() == 0
    assert fake.played == [] and asked == []


def test_nobody_listed_means_nobody_played(monkeypatch) -> None:
    fake = FakeViewer(monkeypatch, [_game()])
    bot, _ = _bot(humans=())
    assert bot.play_round() == 0
    assert fake.played == []


def test_white_side_too(monkeypatch) -> None:
    fake = FakeViewer(monkeypatch, [_game(white="bot-maia-1500", black="cilou", turn="white")])
    bot, _ = _bot(move="e2e4")
    bot.play_round()
    assert fake.played == [("g1", "e2e4", "tok-1500")]


@pytest.mark.parametrize("status", [403, 409, 422, None])
def test_refused_move_is_not_counted(monkeypatch, status) -> None:
    FakeViewer(monkeypatch, [_game()], status=status)
    bot, _ = _bot()
    assert bot.play_round() == 0


def test_engine_failure_sends_nothing(monkeypatch) -> None:
    fake = FakeViewer(monkeypatch, [_game()])
    bot, _ = _bot(move=None)
    assert bot.play_round() == 0
    assert fake.played == []


def test_unreachable_viewer_plays_nothing(monkeypatch) -> None:
    monkeypatch.setattr(viewer, "games_in_progress", lambda url: None)
    bot, asked = _bot()
    assert bot.play_round() == 0 and asked == []


@pytest.mark.parametrize(
    ("account", "elo"),
    [
        ("bot-maia-1100", 1100),
        ("bot-maia-1900", 1900),
        ("bot-maia-2000", None),
        ("bot-maia-150", None),
        ("maia-1500", None),
        ("bot-maia-1500x", None),
    ],
)
def test_level_of(account, elo) -> None:
    assert level_of(account) == elo


def test_read_tokens_only_private_maia_files(tmp_path: Path, caplog) -> None:
    (tmp_path / "bot-maia-1500.token").write_text("tok-1500\n")
    (tmp_path / "bot-maia-1500.token").chmod(0o600)
    (tmp_path / "bot-maia-1900.token").write_text("tok-1900")
    (tmp_path / "bot-maia-1900.token").chmod(0o644)
    (tmp_path / "bot-maia-2500.token").write_text("x")
    (tmp_path / "bot-maia-2500.token").chmod(0o600)
    (tmp_path / "leader.token").write_text("x")
    with caplog.at_level(logging.WARNING):
        assert read_tokens(tmp_path) == {"bot-maia-1500": "tok-1500"}
    assert all("tok-" not in r.getMessage() for r in caplog.records)


def test_parse_humans() -> None:
    assert parse_humans(" Stephane, cilou ,,") == frozenset({"stephane", "cilou"})
    assert parse_humans("") == frozenset()


def test_cli_without_tokens_exits_2(tmp_path: Path) -> None:
    assert main(["--tokens-dir", str(tmp_path), "--once"]) == 2


def test_no_mcp_tool_exposes_maia() -> None:
    """Leader #93 : Maia is never an analysis source for the agents.

    The server never imports the bot, and no MCP tool, schema or handler
    mentions Maia or lc0.
    """
    import palamede_chessboard

    root = Path(palamede_chessboard.__file__).parent
    for path in root.rglob("*.py"):
        assert "palamede_maia" not in path.read_text(encoding="utf-8"), path
    mcp_files = [
        *root.glob("_mcp_*.py"),
        *root.glob("_mcp_tool_schemas/*.py"),
        root / "mcp_chessboard_server.py",
    ]
    assert len(mcp_files) > 3
    for path in mcp_files:
        text = path.read_text(encoding="utf-8").lower()
        assert "maia" not in text and "lc0" not in text, path


def test_bot_module_does_not_import_the_server() -> None:
    source = Path(bot_module.__file__).parent
    for path in source.rglob("*.py"):
        assert "palamede_chessboard" not in path.read_text(encoding="utf-8"), path
