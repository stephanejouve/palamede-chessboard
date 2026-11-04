"""Shared fixtures of the coach tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def engine_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The engine log of the test, never the real one of ``~/.local/share``."""
    path = tmp_path / "engine_calls_log.jsonl"
    monkeypatch.setenv("OUTILLAGES_ENGINE_CALLS_LOG", str(path))
    return path
