"""Durable JSON writes (``palamede_chessboard._atomic``, #34 / #46)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from palamede_chessboard import _atomic


def test_write_json_content_mode_and_no_leftover(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    _atomic.write_json(path, {"b": 1, "a": [2]}, mode=0o600)
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": [2], "b": 1}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_write_json_fsyncs_file_then_parent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Junior on #47 : the rename is durable only once the dir is synced."""
    synced: list[str] = []
    real = os.fsync

    def spy(fd: int) -> None:
        synced.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real(fd)

    monkeypatch.setattr(os, "fsync", spy)
    _atomic.write_json(tmp_path / "x.json", {}, mode=0o644)
    assert synced == ["file", "dir"]


def test_failed_write_leaves_previous_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    _atomic.write_json(path, {"v": 1}, mode=0o600)
    with pytest.raises(TypeError):
        _atomic.write_json(path, {"v": object()}, mode=0o600)
    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
