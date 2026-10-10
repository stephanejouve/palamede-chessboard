"""Placeholder — s'assure que CI pytest a au moins 1 test à exécuter.

À remplacer par les tests métier en Phase 1 (migration tests depuis
outillages).
"""

import re

from palamede_chessboard import __version__


def test_version_is_valid_semver() -> None:
    # Robuste aux bumps release-please (0.0.1 → 0.1.0 → 1.0.0, pré-releases).
    assert re.match(r"^\d+\.\d+\.\d+(?:-[\w.]+)?$", __version__)
