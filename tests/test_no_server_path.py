"""No error said to a client carries a path of the server (#107).

The game errors name the game, the unreadable files name what they are ;
the path itself stays in the server's log. The last test is the
conformity grep the issue asks for : no exception that can reach a client
is built from a path.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from palamede_chessboard.chessboard_engine import EngineUnavailableError, get_engine_path
from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameAlreadyExistsError,
    GameMetadata,
    GameNotFoundError,
    GameState,
)
from palamede_chessboard.clock_moves import check_game
from palamede_chessboard.player_prefs import PlayerPrefsError, prefs_path, set_max_concurrent

PACKAGE = Path(__file__).resolve().parents[1] / "palamede_chessboard"


def _meta(game_id: str = "pathless") -> GameMetadata:
    return GameMetadata(game_id=game_id, white="w", black="b")


def test_a_missing_game_is_named_not_located(tmp_path: Path) -> None:
    with pytest.raises(GameNotFoundError) as caught:
        GameState.load("ghost", games_dir=tmp_path)
    assert str(caught.value) == "game not found: ghost"


def test_check_game_names_the_game_too(tmp_path: Path) -> None:
    with pytest.raises(GameNotFoundError) as caught:
        check_game("ghost", tmp_path)
    assert str(caught.value) == "game not found: ghost"


def test_an_existing_game_is_named_not_located(tmp_path: Path) -> None:
    GameState.create(_meta(), games_dir=tmp_path)
    with pytest.raises(GameAlreadyExistsError) as caught:
        GameState.create(_meta(), games_dir=tmp_path)
    assert str(caught.value) == "game already exists: pathless"


def test_an_unreadable_pgn_is_named_and_its_path_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "broken.pgn").write_text("", encoding="utf-8")
    with caplog.at_level(logging.WARNING), pytest.raises(ChessboardError) as caught:
        GameState.load("broken", games_dir=tmp_path)
    assert str(caught.value) == "PGN of broken is unreadable"
    assert str(tmp_path) in caplog.text


@pytest.mark.parametrize("content", ["{not json", '{"players": []}'])
def test_unreadable_preferences_say_no_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: str
) -> None:
    prefs_path(tmp_path).write_text(content, encoding="utf-8")
    with caplog.at_level(logging.WARNING), pytest.raises(PlayerPrefsError) as caught:
        set_max_concurrent(tmp_path, "stephane", 2)
    assert str(tmp_path) not in str(caught.value)
    assert str(tmp_path) in caplog.text


def test_a_missing_stockfish_override_says_no_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MCP_CHESSBOARD_STOCKFISH", "/opt/secret/stockfish-missing")
    with caplog.at_level(logging.WARNING), pytest.raises(EngineUnavailableError) as caught:
        get_engine_path()
    assert "/opt/secret" not in str(caught.value)
    assert "/opt/secret" in caplog.text


#: An exception built from a path : ``Error(str(path))`` or ``Error(f"...{path}...")``.
PATH_IN_ERROR = re.compile(r"Error\((?:str\(\w*path\w*\)|f[\"'][^\"']*\{\w*(?:path|_dir)\w*[!}:])")
#: Built from a path on purpose, never said to a client : the registry's
#: errors are answered « Registre des joueurs illisible » by the viewer and
#: ``INTERNAL`` by the MCP server.
ALLOWED = {"players/registry.py"}


def test_no_client_facing_exception_is_built_from_a_path() -> None:
    found = []
    for source in sorted(PACKAGE.rglob("*.py")):
        relative = source.relative_to(PACKAGE).as_posix()
        if relative in ALLOWED or relative.startswith("cli_"):
            continue
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if PATH_IN_ERROR.search(line):
                found.append(f"{relative}:{number}: {line.strip()}")
    assert found == []
