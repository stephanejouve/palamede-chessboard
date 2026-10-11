"""Shared fixtures for ``tests/lessons``.

A reusable builder for CD-style analysis .md files : the real
analyses live in ``/Users/Shared/games/analyses`` ; here we write
textual templates to a ``tmp_path`` so the suite is hermetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: Format minimal reproduisant la structure d'une analyse CD : titre H1,
#: ligne "Résultat <X>", section ``## Leçons`` avec quelques leçons
#: taguées, et URL viewer qui porte le game_id canonique.
_TEMPLATE: str = """\
# CD contre {opponent} : analyse de la partie ({game_id})

Partie amicale, serveur d'échecs de l'équipe. Résultat {result}.

## Leçons
{lessons_block}

## Partie complète
1. e4 e5 {result}

## Méthode
Visionneuse : https://chess.alliancejr.eu/?game={game_id}&viewer=post_mortem
"""


def _write_analysis_md(
    tmp_path: Path,
    *,
    filename: str,
    game_id: str,
    result: str,
    lessons: list[str] | None = None,
    opponent: str = "Junior",
) -> Path:
    """Write a CD-style analysis .md to ``tmp_path`` and return its path.

    ``lessons`` is a list of raw lines inserted verbatim between the
    ``## Leçons`` heading and the next ``## Partie complète`` heading.
    None/empty => emits a single blank line (section exists but no
    valid lesson line).
    """
    body = "\n".join(lessons) if lessons else ""
    text = _TEMPLATE.format(
        opponent=opponent,
        game_id=game_id,
        result=result,
        lessons_block=body,
    )
    path = tmp_path / filename
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def write_analysis_md():
    """Factory fixture — tests call it with explicit kwargs."""
    return _write_analysis_md


@pytest.fixture
def sample_analysis_md(tmp_path: Path) -> Path:
    """A canonical, fully-populated analysis .md (result 1-0, 3 leçons).

    Mirrors the structure of
    ``/Users/Shared/games/analyses/analyse_claude-desktop-junior_
    20261010_093251.md`` closely enough that any format drift in the
    template would be caught by :func:`palamede_chessboard.lessons.
    extract_lessons`.
    """
    return _write_analysis_md(
        tmp_path,
        filename="analyse_sample.md",
        game_id="cd-vs-junior-20261010",
        result="1-0",
        lessons=[
            "- [junior, 21...Db6] Une diagonale marche dans les deux sens.",
            "- [claude-desktop, 19.cxb4] Compter les défenseurs des pions.",
            "- [tous, 15...Td8] Colonne ouverte : regarder qui est au bout.",
        ],
    )
