"""Shared fixtures + helpers for the ``tests/mcp/`` suite (split from the
pre-split 1471l ``test_mcp_chessboard_server.py``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _decode(result) -> dict:
    """Strip the wrapping ``[TextContent]`` and JSON-parse the payload."""
    assert len(result) == 1
    payload = json.loads(result[0].text)
    payload.pop("_metadata", None)
    return payload


@pytest.fixture
def games_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    # Redirect the per-user board-facts journal into the test's tmp_path
    # so tests don't touch the real ``~/.local/share/outillages/`` and
    # can assert on the file's presence/content cleanly.
    monkeypatch.setenv("OUTILLAGES_BOARD_FACTS_LOG", str(tmp_path / "board_facts_log.jsonl"))
    # Isolate the engine-call audit log too — the guard writes one line
    # per guarded tool call and we don't want to pollute the real
    # ``~/.local/share/outillages/engine_calls_log.jsonl``.
    monkeypatch.setenv("OUTILLAGES_ENGINE_CALLS_LOG", str(tmp_path / "engine_calls_log.jsonl"))
    # Publish a fake arbiter-daemon pidfile pointing at the current test
    # process so ``chess_create_game`` accepts calls that omit the
    # ``arbiter`` field. Individual tests that want to verify the
    # arbiter-required contract can delete the file explicitly.
    (tmp_path / ".arbiter-daemon.pid").write_text(str(os.getpid()), encoding="utf-8")
    return tmp_path
