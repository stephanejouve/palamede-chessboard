"""PGN header values cannot break out of their ``[Tag "value"]`` (#128).

``GameState.save`` wrote every header as ``[{tag} "{value}"]`` : a value
holding ``"`` or a line break closed its header and added others, e.g. a
time control ``5+3"]\\n[Rated "true`` turned an unrated game into a rated
one once read back. python-chess neither writes nor reads back the PGN
escapes (``\\"``, ``\\\\``), so the values that would need them are refused
before anything is written, and every accepted value survives a save and
a load unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameMetadata,
    GameState,
    TimeControl,
)

FORBIDDEN = ['"', "\\", "{", "}", "\n", "\r", "\t", "\x00", "\x1b", "\x7f", "\x85"]
FIELDS = ["event", "site", "round", "white", "black", "annotator", "opening", "eco"]


def _meta(game_id: str = "headers", **fields: object) -> GameMetadata:
    values: dict = {
        "game_id": game_id,
        "event": "Test",
        "site": "pytest",
        "round": "1",
        "white": "white-player",
        "black": "black-player",
        "time_control": TimeControl(format="5+3"),
        "rated": False,
    }
    values.update(fields)
    return GameMetadata(**values)


def _files(games_dir: Path) -> list[str]:
    return sorted(p.name for p in games_dir.iterdir()) if games_dir.exists() else []


@pytest.mark.parametrize(
    "value",
    [
        "Partie amicale à Cunlhat",
        "O'Neill",
        "[crochets] et (parenthèses)",
        "Tournoi 2026 — ronde 3 ; tour 2",
        "Ελληνικά 漢字 ♞",
        "trailing space ",
    ],
)
def test_an_accepted_value_survives_save_and_load(tmp_path: Path, value: str) -> None:
    meta = _meta(
        event=value,
        site=value,
        white=value,
        black=value,
        annotator=value,
        opening=value,
        eco=value,
    )
    GameState.create(meta, games_dir=tmp_path)
    loaded = GameState.load("headers", games_dir=tmp_path)
    assert loaded.metadata.to_pgn_headers() == meta.to_pgn_headers()


def test_the_injection_of_the_issue_is_refused(tmp_path: Path) -> None:
    forged = TimeControl(format='5+3"]\n[Rated "true')
    with pytest.raises(ChessboardError, match="TimeControl"):
        GameState.create(_meta(time_control=forged), games_dir=tmp_path)
    assert _files(tmp_path) == []


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("char", FORBIDDEN)
def test_a_forbidden_character_is_refused_before_anything_is_written(
    tmp_path: Path, field: str, char: str
) -> None:
    with pytest.raises(ChessboardError, match="not allowed"):
        GameState.create(_meta(**{field: f"a{char}b"}), games_dir=tmp_path)
    assert _files(tmp_path) == []


def test_a_spectator_is_checked_too(tmp_path: Path) -> None:
    with pytest.raises(ChessboardError, match="Spectators"):
        GameState.create(_meta(spectators=["ok", 'x"]']), games_dir=tmp_path)


def test_a_refused_save_leaves_the_previous_pgn_untouched(tmp_path: Path) -> None:
    state = GameState.create(_meta(), games_dir=tmp_path)
    state.play_move("e4")
    before = (tmp_path / "headers.pgn").read_text(encoding="utf-8")
    state.metadata = replace(state.metadata, opening="}{ 1-0")
    with pytest.raises(ChessboardError):
        state.play_move("e5")
    assert (tmp_path / "headers.pgn").read_text(encoding="utf-8") == before
    assert not (tmp_path / "headers.pgn.tmp").exists()


def test_a_name_with_braces_cannot_close_the_termination_comment(tmp_path: Path) -> None:
    # The resignation note quotes the loser's name in ``{...}`` before the
    # result : the name is refused at creation, so a resignation always saves.
    with pytest.raises(ChessboardError, match="White"):
        GameState.create(_meta(white="w} 1-0 {"), games_dir=tmp_path)
    state = GameState.create(_meta("resign"), games_dir=tmp_path)
    state.resign(by="white")
    loaded = GameState.load("resign", games_dir=tmp_path)
    assert loaded.metadata.result == "0-1"
    assert loaded.metadata.termination_note == "white-player resigns"
