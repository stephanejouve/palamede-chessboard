"""Shared fixtures and helpers for ``tests/viewer``.

Extracted from the monolithic ``tests/test_chessboard_viewer.py``
(785 lines, split per CD review 2026-10-11 msg 57647) : the two
pytest fixtures (``client``, ``games_dir``) and the ``_create_game``
helper are used by every sibling test file under the viewer domain.

``_create_game`` is a plain function (not a fixture) because tests
build multiple games with different ``game_id`` / ``result`` /
``time_control`` combinations in the same test body — a fixture
would need to return a factory, which is strictly more verbose.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from palamede_chessboard.chessboard_state import GameMetadata, GameState, TimeControl
from palamede_chessboard.chessboard_viewer import build_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    return TestClient(build_app())


@pytest.fixture
def games_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    return tmp_path


def _create_game(
    games_dir: Path,
    *,
    game_id: str = "viewer-test",
    time_control: TimeControl | None = None,
    result: str = "*",
) -> GameState:
    # Volet 3a : the TimeControl default is now "30+30" (clock-enabled).
    # Viewer tests that don't care about the clock stay deterministic by
    # pinning to "unrated" when the caller doesn't pass a time_control.
    # TestClock explicitly passes "5+3" so it's not affected.
    state = GameState.create(
        GameMetadata(
            game_id=game_id,
            white="w",
            black="b",
            time_control=time_control or TimeControl(format="unrated"),
            result=result,
        ),
        games_dir=games_dir,
    )
    state.metadata.result = result
    state.save()
    return state
