"""Unit tests for :func:`palamede_chessboard.lessons.aggregate_lessons`.

Covers idempotence (same scan twice = zero new lessons the 2nd time),
multi-analysis aggregation, output file creation from scratch, and the
skip-on-in-progress rule.
"""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.lessons import aggregate_lessons


def test_aggregate_creates_output_from_scratch(tmp_path: Path, write_analysis_md):
    """First run creates ``output`` and returns the count of lessons."""
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    write_analysis_md(
        analyses,
        filename="a1.md",
        game_id="g1",
        result="1-0",
        lessons=[
            "- [junior, 1.e4] leçon 1",
            "- [claude-desktop, 2.e5] leçon 2",
        ],
    )
    output = tmp_path / "carnet.md"
    added = aggregate_lessons(analyses, output)
    assert added == 2
    text = output.read_text(encoding="utf-8")
    assert "## g1" in text
    assert "- [junior, 1.e4] leçon 1" in text
    assert "- [claude-desktop, 2.e5] leçon 2" in text


def test_aggregate_idempotent_on_rescan(tmp_path: Path, write_analysis_md):
    """Un rescan sans nouveau .md n'ajoute rien et renvoie 0."""
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    write_analysis_md(
        analyses,
        filename="a1.md",
        game_id="g1",
        result="1-0",
        lessons=["- [junior, 1.e4] leçon unique"],
    )
    output = tmp_path / "carnet.md"
    first = aggregate_lessons(analyses, output)
    assert first == 1
    size_after_first = output.stat().st_size
    second = aggregate_lessons(analyses, output)
    assert second == 0
    assert output.stat().st_size == size_after_first


def test_aggregate_appends_new_analysis(tmp_path: Path, write_analysis_md):
    """Une seconde analyse apparue après le 1er run est bien ajoutée."""
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    write_analysis_md(
        analyses,
        filename="a1.md",
        game_id="g1",
        result="1-0",
        lessons=["- [junior, 1.e4] leçon 1"],
    )
    output = tmp_path / "carnet.md"
    assert aggregate_lessons(analyses, output) == 1
    write_analysis_md(
        analyses,
        filename="a2.md",
        game_id="g2",
        result="0-1",
        lessons=[
            "- [claude-desktop, 7.Nc3] leçon 2a",
            "- [tous, 15...Rd8] leçon 2b",
        ],
    )
    assert aggregate_lessons(analyses, output) == 2
    text = output.read_text(encoding="utf-8")
    assert "## g1" in text
    assert "## g2" in text
    assert text.count("## g1") == 1
    assert text.count("## g2") == 1


def test_aggregate_skips_in_progress(tmp_path: Path, write_analysis_md):
    """Une analyse avec Result ``*`` est ignorée — CD rule 2."""
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    write_analysis_md(
        analyses,
        filename="live.md",
        game_id="live-game",
        result="*",
        lessons=["- [junior, 10...Nf6] leçon fantôme"],
    )
    output = tmp_path / "carnet.md"
    assert aggregate_lessons(analyses, output) == 0
    assert not output.exists()


def test_aggregate_missing_analyses_dir(tmp_path: Path):
    """Un répertoire d'analyses inexistant → ``0`` et pas d'output créé."""
    output = tmp_path / "carnet.md"
    assert aggregate_lessons(tmp_path / "nope", output) == 0
    assert not output.exists()


def test_aggregate_sorted_scan(tmp_path: Path, write_analysis_md):
    """L'ordre des blocs dans l'output suit l'ordre alphabétique des .md."""
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    write_analysis_md(
        analyses,
        filename="b.md",
        game_id="g-b",
        result="1-0",
        lessons=["- [junior, 2.e5] leçon B"],
    )
    write_analysis_md(
        analyses,
        filename="a.md",
        game_id="g-a",
        result="1-0",
        lessons=["- [junior, 1.e4] leçon A"],
    )
    output = tmp_path / "carnet.md"
    added = aggregate_lessons(analyses, output)
    assert added == 2
    text = output.read_text(encoding="utf-8")
    assert text.index("## g-a") < text.index("## g-b")


def test_aggregate_creates_parent_dir(tmp_path: Path, write_analysis_md):
    """Le parent de ``output`` est créé si besoin (mkdir(parents=True))."""
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    write_analysis_md(
        analyses,
        filename="a.md",
        game_id="g",
        result="1-0",
        lessons=["- [junior, 1.e4] leçon"],
    )
    output = tmp_path / "nested" / "deep" / "carnet.md"
    assert aggregate_lessons(analyses, output) == 1
    assert output.exists()


def test_aggregate_preserves_existing_content(tmp_path: Path, write_analysis_md):
    """L'append ne tronque pas le contenu existant du carnet."""
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    output = tmp_path / "carnet.md"
    output.write_text("## g-old\n\n- [tous, 0] déjà là\n\n", encoding="utf-8")
    write_analysis_md(
        analyses,
        filename="new.md",
        game_id="g-new",
        result="1-0",
        lessons=["- [junior, 1.e4] nouvelle leçon"],
    )
    added = aggregate_lessons(analyses, output)
    assert added == 1
    text = output.read_text(encoding="utf-8")
    assert "## g-old" in text
    assert "## g-new" in text
    assert "déjà là" in text
