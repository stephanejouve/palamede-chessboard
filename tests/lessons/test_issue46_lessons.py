"""Carnet de leçons : suivis de #46 (repris de #27 §5)."""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from palamede_chessboard import cli_lessons, lessons
from palamede_chessboard.chessboard_state import GameMetadata, GameState
from palamede_chessboard.lessons import (
    DEFAULT_OUTPUT_FILENAME,
    LessonsError,
    aggregate_lessons,
    extract_lessons,
)
from tests.lessons.conftest import _write_analysis_md

LESSON = ["- [junior, 12.Nf3] Contrôler le centre avant d'attaquer."]


def _pgn(games_dir: Path, game_id: str, result: str) -> None:
    state = GameState.create(
        GameMetadata(game_id=game_id, white="w", black="b"), games_dir=games_dir
    )
    state.metadata.result = result
    state.save()


# ---- le résultat fait foi dans le PGN -----------------------------------


def test_pgn_in_progress_beats_md_result(tmp_path: Path) -> None:
    """Fair-play : l'analyse annonce 1-0 mais la partie est encore en cours."""
    _pgn(tmp_path, "g1", "*")
    md = _write_analysis_md(tmp_path, filename="a.md", game_id="g1", result="1-0", lessons=LESSON)
    assert extract_lessons(md, tmp_path) == []


def test_pgn_finished_beats_md_without_result(tmp_path: Path) -> None:
    _pgn(tmp_path, "g2", "0-1")
    md = _write_analysis_md(tmp_path, filename="a.md", game_id="g2", result="*", lessons=LESSON)
    assert [lesson.game_id for lesson in extract_lessons(md, tmp_path)] == ["g2"]


def test_md_result_is_fallback_without_pgn(tmp_path: Path) -> None:
    md = _write_analysis_md(
        tmp_path, filename="a.md", game_id="ghost", result="1-0", lessons=LESSON
    )
    assert len(extract_lessons(md, tmp_path)) == 1


# ---- encodage ------------------------------------------------------------


def test_non_utf8_analysis_is_skipped_not_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    (analyses / "0-latin1.md").write_bytes("Résultat 1-0\n## Leçons\n".encode("latin-1"))
    _write_analysis_md(analyses, filename="1-ok.md", game_id="ok", result="1-0", lessons=LESSON)
    output = tmp_path / "carnet.md"
    with caplog.at_level("WARNING"):
        assert aggregate_lessons(analyses, output) == 1
    assert "0-latin1.md" in caplog.text
    assert "## ok" in output.read_text(encoding="utf-8")


def test_unreadable_output_is_refused_and_left_intact(tmp_path: Path) -> None:
    """Le relire comme vide ferait tout réajouter en double."""
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    _write_analysis_md(analyses, filename="a.md", game_id="g", result="1-0", lessons=LESSON)
    output = tmp_path / "carnet.md"
    corrupt = "## g\n\n- [junior, 1.e4] é\n".encode("latin-1")
    output.write_bytes(corrupt)
    with pytest.raises(LessonsError, match="rien n'est ajouté"):
        aggregate_lessons(analyses, output)
    assert output.read_bytes() == corrupt


# ---- verrou --------------------------------------------------------------


def test_concurrent_runs_add_each_game_once(tmp_path: Path) -> None:
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    for i in range(20):
        _write_analysis_md(
            analyses, filename=f"{i:02}.md", game_id=f"g{i:02}", result="1-0", lessons=LESSON
        )
    output = tmp_path / "carnet.md"
    with ThreadPoolExecutor(max_workers=8) as pool:
        added = list(pool.map(lambda _: aggregate_lessons(analyses, output), range(8)))
    assert sum(added) == 20
    headings = [
        ln for ln in output.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")
    ]
    assert sorted(headings) == [f"## g{i:02}" for i in range(20)]


def test_read_and_append_happen_under_flock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    _write_analysis_md(analyses, filename="a.md", game_id="g", result="1-0", lessons=LESSON)
    ops: list[int] = []
    real = lessons.fcntl.flock
    monkeypatch.setattr(lessons.fcntl, "flock", lambda fd, op: (ops.append(op), real(fd, op)))
    aggregate_lessons(analyses, tmp_path / "carnet.md")
    assert ops == [lessons.fcntl.LOCK_EX, lessons.fcntl.LOCK_UN]


# ---- CLI ---------------------------------------------------------------


def test_cli_defaults_follow_games_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    _pgn(tmp_path, "g", "1-0")
    _write_analysis_md(analyses, filename="a.md", game_id="g", result="*", lessons=LESSON)
    assert cli_lessons.main([]) == 0
    assert "## g" in (tmp_path / DEFAULT_OUTPUT_FILENAME).read_text(encoding="utf-8")
    assert "1 nouvelles leçons" in capsys.readouterr().out


def test_cli_returns_1_on_unreadable_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    _write_analysis_md(analyses, filename="a.md", game_id="g", result="1-0", lessons=LESSON)
    output = tmp_path / "carnet.md"
    output.write_bytes(b"\xff\xfe")
    assert cli_lessons.main(["--analyses", str(analyses), "--output", str(output)]) == 1
    assert "illisible" in capsys.readouterr().err


def test_cli_help_explains_regeneration(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli_lessons.main(["--help"])
    assert "supprimer son bloc" in capsys.readouterr().out


# ---- import léger --------------------------------------------------------


def test_package_import_is_light_and_lazy() -> None:
    code = (
        "import sys, palamede_chessboard as p\n"
        "heavy = [m for m in ('palamede_chessboard.lessons', 'palamede_chessboard.chessboard_state', 'chess')"
        " if m in sys.modules]\n"
        "print(heavy)\n"
        "print(p.aggregate_lessons.__module__)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert out == ["[]", "palamede_chessboard.lessons"]
