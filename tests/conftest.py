"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

#: Never created : the tests see no per-user settings file.
NO_CONFIG = Path(__file__).with_name("no-user-config.toml")


@pytest.fixture(autouse=True)
def _no_user_config(monkeypatch) -> None:
    """The bridge's per-user settings (#90) : never the developer's own file in a test."""
    monkeypatch.setenv("PALAMEDE_CONFIG", str(NO_CONFIG))
