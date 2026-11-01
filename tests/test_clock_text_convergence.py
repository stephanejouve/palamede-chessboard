"""The viewer and the Tablier show the clock the same way (#147).

``m:ss`` (``h:mm:ss`` past an hour), no tenths, rounded up (owner, 01/11) :
one ``clockText`` in each face, kept identical, so that they cannot drift
apart again (« 9s.3 » in the viewer, « 9.3 » in the Tablier).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "palamede_chessboard" / "templates" / "viewer" / "_poll.js.jinja"
TABLIER = ROOT / "chessnut" / "palamede_chessnut" / "web" / "app.js"
FUNCTION = re.compile(r"^function clockText\(seconds\) \{\n.*?^\}\n", re.MULTILINE | re.DOTALL)


def clock_text(path: Path) -> str:
    match = FUNCTION.search(path.read_text(encoding="utf-8"))
    assert match, f"no clockText in {path.name}"
    return match.group(0)


def test_both_faces_share_one_clock_text() -> None:
    assert clock_text(VIEWER) == clock_text(TABLIER)


def test_no_tenths_and_rounded_up() -> None:
    body = clock_text(TABLIER)
    assert "Math.ceil(seconds)" in body
    assert "toFixed" not in body
