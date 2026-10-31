"""palamede-chessboard — serveur MCP de parties d'échecs multi-agents.

L'import du paquet reste léger (#46) : l'API du carnet de leçons
(:mod:`palamede_chessboard.lessons`) est réexportée à la demande, au
premier accès à l'un de ses noms.
"""

from __future__ import annotations

import importlib
from typing import Any

__version__ = "0.15.1"  # x-release-please-version

__all__ = [
    "DEFAULT_OUTPUT_FILENAME",
    "Lesson",
    "TERMINAL_RESULTS",
    "aggregate_lessons",
    "extract_lessons",
]


def __dir__() -> list[str]:
    """Keep the lazy re-exports visible to ``dir()`` and completion."""
    return sorted({*globals(), *__all__})


def __getattr__(name: str) -> Any:
    """Lazy re-export of the lessons API (PEP 562)."""
    if name in __all__:
        return getattr(importlib.import_module("palamede_chessboard.lessons"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
