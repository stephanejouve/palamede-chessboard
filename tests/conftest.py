"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

#: Never created : the tests see no per-user settings file.
NO_CONFIG_HOME = Path(__file__).with_name("no-user-config-home")


@pytest.fixture(autouse=True)
def _no_user_config(monkeypatch) -> None:
    """The bridge's per-user settings (#90) : never the developer's own file in a test.

    The default place, not ``PALAMEDE_CONFIG`` : a file it names must exist.
    """
    monkeypatch.delenv("PALAMEDE_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(NO_CONFIG_HOME))
