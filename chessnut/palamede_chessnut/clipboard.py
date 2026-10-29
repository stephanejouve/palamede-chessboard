"""The system clipboard for the window's « Édition ▸ Copier … » menus (#140).

A copy asked from a native menu runs outside any click in the page, and
WebKit refuses the page's clipboard without one : so the bridge copies
itself when the platform gives Python a clipboard (macOS, through AppKit,
which pywebview already brings). Elsewhere, :func:`system_copy` is ``None``
and the page tries, showing the text selected when its copy is refused.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

#: Puts ``text`` on the clipboard ; ``True`` when it is there.
Copy = Callable[[str], bool]


def system_copy() -> Copy | None:
    """The macOS clipboard, or ``None`` where Python has none to offer."""
    try:
        from AppKit import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
            NSPasteboard,
            NSPasteboardTypeString,
        )
    except ImportError:
        return None

    def copy(text: str) -> bool:
        board = NSPasteboard.generalPasteboard()
        board.clearContents()
        return bool(board.setString_forType_(text, NSPasteboardTypeString))

    return copy
