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
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from palamede_chessboard.lessons import DEFAULT_OUTPUT_FILENAME, aggregate_lessons

#: Répertoire par défaut des analyses CD.
DEFAULT_ANALYSES_DIR: Path = Path("/Users/Shared/games/analyses")

#: Fichier cumulé par défaut : vit à côté des analyses pour que les
#: joueurs retrouvent les deux au même endroit (``/Users/Shared/games``).
DEFAULT_OUTPUT_PATH: Path = Path("/Users/Shared/games") / DEFAULT_OUTPUT_FILENAME


def _build_parser() -> argparse.ArgumentParser:
    """Return the argparse parser for ``palamede-lessons``."""
    parser = argparse.ArgumentParser(
        prog="palamede-lessons",
        description=(
            "Agrège les sections '## Leçons' des analyses .md CD dans un "
            "fichier Markdown cumulé idempotent."
        ),
    )
    parser.add_argument(
        "--analyses",
        type=Path,
        default=DEFAULT_ANALYSES_DIR,
        help=f"Répertoire des analyses .md (défaut : {DEFAULT_ANALYSES_DIR}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Fichier Markdown cumulé (défaut : {DEFAULT_OUTPUT_PATH}).",
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
    added = aggregate_lessons(analyses_dir=args.analyses, output=args.output)
    print(f"{added} nouvelles leçons ajoutées à {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
