"""The Tablier is released on its own (release-please component ``chessnut``).

release-please bumps ``chessnut/pyproject.toml``, the manifest and, through
``extra-files``, ``__version__`` : the three must tell the same version, the
one the journal shows at start-up.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from palamede_chessnut import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject_and_manifest() -> None:
    pyproject = tomllib.loads((ROOT / "chessnut" / "pyproject.toml").read_text())
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text())
    assert __version__ == pyproject["project"]["version"] == manifest["chessnut"]


def test_root_package_leaves_the_tablier_to_its_component() -> None:
    config = json.loads((ROOT / ".release-please-config.json").read_text())
    packages = config["packages"]
    assert "chessnut" in packages["."]["exclude-paths"]
    assert packages["chessnut"]["component"] == "palamede-chessnut"
    assert packages["chessnut"]["include-component-in-tag"] is True


def test_only_the_tablier_is_released_as_0_2_0() -> None:
    """Its first release (owner, 03/11) : a fix alone would make it 0.1.1. The key
    goes once palamede-chessnut-v0.2.0 exists, the other packages never carry it."""
    config = json.loads((ROOT / ".release-please-config.json").read_text())
    packages = config["packages"]
    assert packages["chessnut"]["release-as"] == "0.2.0"
    assert "release-as" not in config
    assert all("release-as" not in packages[p] for p in packages if p != "chessnut")
