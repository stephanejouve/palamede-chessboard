"""Which build of the Tablier runs : the release, and the branch and commit it was installed from.

``uv tool install 'palamede-chessnut @ git+…@<branch>'`` records the branch
asked and the commit resolved in the installed package's ``direct_url.json``
(PEP 610). A smoke can then tell which head it ran, without looking for
that file by hand (Stéphane, smoke of #226, 05/11).
"""

from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path

from palamede_chessnut import __version__

DISTRIBUTION = "palamede-chessnut"


def origin() -> dict[str, str]:
    """``{"branch", "commit"}`` of a git install, ``{"path"}`` of a local one, else empty."""
    try:
        raw = metadata.distribution(DISTRIBUTION).read_text("direct_url.json")
        found = json.loads(raw) if raw else {}
    except (metadata.PackageNotFoundError, ValueError):
        return {}
    if not isinstance(found, dict):
        return {}
    vcs = found.get("vcs_info")
    if isinstance(vcs, dict) and isinstance(vcs.get("commit_id"), str):
        branch = vcs.get("requested_revision")
        return {"branch": branch if isinstance(branch, str) else "", "commit": vcs["commit_id"]}
    url = found.get("url")
    if isinstance(url, str) and url.startswith("file://"):
        # The code loaded, not the install's origin : an editable install may
        # point to a copy since moved (review Admin #228), and the URL is not
        # decoded (%20, /C:/, review Junior #228).
        return {"path": str(Path(__file__).resolve().parent.parent)}
    return {}


def build_info() -> str:
    """« palamede-chessnut 0.4.0, branche feat/x, commit 2087b3c », for the journal and « À propos »."""
    text = f"palamede-chessnut {__version__}"
    found = origin()
    if "commit" in found:
        branch = found["branch"] or "par défaut"
        return f"{text}, branche {branch}, commit {found['commit'][:7]}"
    if "path" in found:
        return f"{text}, copie locale {found['path']}"
    return text
