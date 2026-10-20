"""palamede-chessboard — serveur MCP de parties d'échecs multi-agents.

Phase 0 skeleton. Les modules métier seront extraits de ``outillages``
en Phase 1 et refactorés en Phase 2.
"""

from palamede_chessboard.lessons import (
    DEFAULT_OUTPUT_FILENAME,
    TERMINAL_RESULTS,
    Lesson,
    aggregate_lessons,
    extract_lessons,
)

__version__ = "0.4.0"  # x-release-please-version

__all__ = [
    "DEFAULT_OUTPUT_FILENAME",
    "Lesson",
    "TERMINAL_RESULTS",
    "aggregate_lessons",
    "extract_lessons",
]
