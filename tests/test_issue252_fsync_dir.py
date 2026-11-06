"""#252 : the arbiter's cursor and pidfile, and the observations' rotation, fsync
the parent directory after their rename (following #246).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard import _atomic, chess_arbiter_daemon
from palamede_chessboard.chess_arbiter_daemon import (
    _write_cursor,
    arbiter_pidfile_name,
    pidfile_lifecycle,
)
from palamede_chessboard.players import observe


def _spy(monkeypatch: pytest.MonkeyPatch, module: object) -> list[tuple[Path, list[str]]]:
    """Record each fsync_dir call with the directory listing at that moment."""
    calls: list[tuple[Path, list[str]]] = []

    def spy(directory: Path) -> None:
        calls.append((directory, sorted(p.name for p in directory.iterdir())))

    monkeypatch.setattr(module, "fsync_dir", spy)
    return calls


def test_cursor_fsyncs_parent_after_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _spy(monkeypatch, chess_arbiter_daemon)
    cursor = tmp_path / "arbiter" / "g.cursor"
    _write_cursor(cursor, 7)
    assert calls == [(cursor.parent, ["g.cursor"])]
    assert cursor.read_text(encoding="utf-8") == "7"


def test_pidfile_fsyncs_parent_after_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, chess_arbiter_daemon)
    with pidfile_lifecycle(tmp_path, "g1"):
        assert calls == [(tmp_path, [arbiter_pidfile_name("g1")])]


def test_rotation_fsyncs_parent_after_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, _atomic)
    monkeypatch.setattr(observe, "MAX_JOURNAL_BYTES", 10)
    journal = tmp_path / "observations.jsonl"
    journal.write_text("x" * 20 + "\n", encoding="utf-8")
    observe._append(journal, {"tool": "t"})
    # The rename is seen : the old journal under .1, the new one not yet written.
    assert len(calls) == 1 and calls[0][0] == tmp_path
    seen = [n for n in calls[0][1] if not n.endswith(".lock")]
    assert seen == ["observations.jsonl.1"]
    assert journal.read_text(encoding="utf-8").strip() == '{"tool": "t"}'


def test_no_rotation_no_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _spy(monkeypatch, _atomic)
    observe._append(tmp_path / "observations.jsonl", {"tool": "t"})
    assert calls == []
