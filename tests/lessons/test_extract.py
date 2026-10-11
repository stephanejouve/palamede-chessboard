"""Unit tests for :func:`palamede_chessboard.lessons.extract_lessons`.

Covers the strict tag format, the ``Result == *`` skip rule, the
``game_id`` extraction priority (URL viewer first, filename stem
fallback), and corner cases (missing file, missing Leçons section,
unmatched lines).
"""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.lessons import Lesson, extract_lessons


def test_extract_lessons_canonical(sample_analysis_md: Path):
    """A canonical analysis yields one :class:`Lesson` per tag line."""
    lessons = extract_lessons(sample_analysis_md)
    assert len(lessons) == 3
    assert all(isinstance(lesson, Lesson) for lesson in lessons)
    first = lessons[0]
    assert first.game_id == "cd-vs-junior-20261010"
    assert first.player == "junior"
    assert first.move == "21...Db6"
    assert first.text == "Une diagonale marche dans les deux sens."
    assert first.source_md == sample_analysis_md


def test_extract_lessons_skips_in_progress(tmp_path: Path, write_analysis_md):
    """Un résultat ``*`` fait sauter TOUTE l'analyse — CD rule 2."""
    md_path = write_analysis_md(
        tmp_path,
        filename="live.md",
        game_id="live-game",
        result="*",
        lessons=["- [junior, 10...Nf6] leçon fantôme ignorée"],
    )
    assert extract_lessons(md_path) == []


def test_extract_lessons_result_draw_accepted(tmp_path: Path, write_analysis_md):
    """Nulle (``1/2-1/2``) est un résultat terminal, pas un skip."""
    md_path = write_analysis_md(
        tmp_path,
        filename="draw.md",
        game_id="draw-game",
        result="1/2-1/2",
        lessons=["- [junior, 42...Kh8] remise sur accord."],
    )
    lessons = extract_lessons(md_path)
    assert len(lessons) == 1
    assert lessons[0].text == "remise sur accord."


def test_extract_lessons_strict_tag_format(tmp_path: Path, write_analysis_md):
    """Les lignes qui ne matchent pas ``- [player, move] text`` sont ignorées."""
    md_path = write_analysis_md(
        tmp_path,
        filename="mixed.md",
        game_id="mixed-game",
        result="0-1",
        lessons=[
            "- [junior, 21...Db6] leçon valide 1",
            "- une puce sans tag",
            "prose libre",
            "- [claude-desktop, 19.cxb4] leçon valide 2",
            "- [bad format text",
            "",
        ],
    )
    lessons = extract_lessons(md_path)
    assert [lesson.text for lesson in lessons] == [
        "leçon valide 1",
        "leçon valide 2",
    ]


def test_extract_lessons_missing_file(tmp_path: Path):
    """Fichier inexistant → ``[]`` (soft-fail, pas d'exception)."""
    assert extract_lessons(tmp_path / "nope.md") == []


def test_extract_lessons_missing_lecons_section(tmp_path: Path):
    """``## Leçons`` absente → ``[]`` même si la partie a un résultat."""
    md_path = tmp_path / "no_section.md"
    md_path.write_text(
        "# Titre (game-xyz)\n\nRésultat 1-0.\n\n## Bilan\nRAS.\n",
        encoding="utf-8",
    )
    assert extract_lessons(md_path) == []


def test_extract_lessons_game_id_falls_back_to_stem(tmp_path: Path):
    """Si l'URL viewer est absente, le stem du .md sert de ``game_id``."""
    md_path = tmp_path / "stem_fallback.md"
    md_path.write_text(
        "# Analyse (whatever)\n\nRésultat 1-0.\n\n"
        "## Leçons\n- [junior, 1.e4] premier coup central.\n\n"
        "## Fin\n",
        encoding="utf-8",
    )
    lessons = extract_lessons(md_path)
    assert len(lessons) == 1
    assert lessons[0].game_id == "stem_fallback"


def test_extract_lessons_result_from_tail_fallback(tmp_path: Path):
    """Pas de mention « Résultat » : token terminal en fin de fichier.

    Sans URL viewer dans le .md, ``game_id`` tombe sur le stem — c'est
    le comportement documenté du fallback (le H1 n'est PAS parsé).
    """
    md_path = tmp_path / "tail-game.md"
    md_path.write_text(
        "# Partie (whatever)\n\n"
        "## Leçons\n- [junior, 42...Kh8] test fallback.\n\n"
        "## Partie complète\n1. e4 e5 1-0\n",
        encoding="utf-8",
    )
    lessons = extract_lessons(md_path)
    assert len(lessons) == 1
    assert lessons[0].game_id == "tail-game"


def test_extract_lessons_empty_section(tmp_path: Path, write_analysis_md):
    """Section Leçons vide → ``[]`` (aucune ligne ne matche le tag)."""
    md_path = write_analysis_md(
        tmp_path,
        filename="empty_section.md",
        game_id="empty-game",
        result="1-0",
        lessons=None,
    )
    assert extract_lessons(md_path) == []
