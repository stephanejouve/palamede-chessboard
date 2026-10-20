"""CLI pour agréger les leçons des analyses CD dans un carnet cumulé.

BT-006 V3 (palamede-chessboard#11) : exécutable périodique (cron /
lancement manuel). Scanne ``analyses_dir``, append idempotent dans
``output``, log le nombre de nouvelles leçons ajoutées.

Usage ::

    palamede-lessons                                   # defaults
    palamede-lessons --analyses /path/to/analyses
    palamede-lessons --output /path/to/carnet.md
    palamede-lessons --analyses /tmp/a --output /tmp/c.md

Entry point : ``palamede-lessons`` (voir ``pyproject.toml``). Peut
aussi être lancé via ``python -m palamede_chessboard.cli_lessons``.

Les chemins par défaut dérivent du dossier des parties
(``MCP_CHESSBOARD_GAMES_DIR``, #14 / #46) : ``<games>/analyses`` et
``<games>/lessons_cumulative.md``, résolus au lancement. Le résultat
de chaque partie est lu dans son PGN de ce même dossier.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from palamede_chessboard.lessons import DEFAULT_OUTPUT_FILENAME, LessonsError, aggregate_lessons
from palamede_chessboard.paths import resolve_games_dir

#: Sous-dossier des analyses CD dans le dossier des parties.
ANALYSES_SUBDIR: str = "analyses"

_EPILOG = (
    "L'ajout est idempotent : une partie déjà présente dans le carnet "
    "(titre « ## <game_id> ») n'est pas réécrite, même si son analyse a "
    "changé. Pour régénérer une partie, supprimer son bloc du carnet "
    "puis relancer."
)


def _build_parser() -> argparse.ArgumentParser:
    """Return the argparse parser for ``palamede-lessons``."""
    parser = argparse.ArgumentParser(
        prog="palamede-lessons",
        description=(
            "Agrège les sections '## Leçons' des analyses .md CD dans un "
            "fichier Markdown cumulé idempotent."
        ),
        epilog=_EPILOG,
    )
    parser.add_argument(
        "--analyses",
        type=Path,
        default=None,
        help=f"Répertoire des analyses .md (défaut : <dossier des parties>/{ANALYSES_SUBDIR}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Fichier Markdown cumulé (défaut : <dossier des parties>/{DEFAULT_OUTPUT_FILENAME}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the shell exit code (``0`` on success).

    Side effect : prints a one-line summary to stdout
    (``N nouvelles leçons ajoutées à <path>``). The number is also the
    return value of :func:`aggregate_lessons`, so a scheduled runner
    can grep that line for observability.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    games_dir = resolve_games_dir()
    analyses = args.analyses or games_dir / ANALYSES_SUBDIR
    output = args.output or games_dir / DEFAULT_OUTPUT_FILENAME
    try:
        added = aggregate_lessons(analyses_dir=analyses, output=output, games_dir=games_dir)
    except LessonsError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    print(f"{added} nouvelles leçons ajoutées à {output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
