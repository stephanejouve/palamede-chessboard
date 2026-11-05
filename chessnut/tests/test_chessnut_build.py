"""Which build runs (Stéphane, smoke of #226) : release, branch and commit from PEP 610."""

from __future__ import annotations

import json
from importlib import metadata

import pytest
from palamede_chessnut import __version__, build


class FakeDistribution:
    def __init__(self, direct_url: object) -> None:
        self.direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        assert name == "direct_url.json"
        if self.direct_url is None:
            return None
        return self.direct_url if isinstance(self.direct_url, str) else json.dumps(self.direct_url)


def installed(monkeypatch, direct_url: object) -> None:
    monkeypatch.setattr(build.metadata, "distribution", lambda _name: FakeDistribution(direct_url))


GIT = {
    "url": "https://github.com/stephanejouve/palamede-chessboard",
    "vcs_info": {
        "vcs": "git",
        "requested_revision": "feat/tablier-refused-move-signal",
        "commit_id": "2087b3c0007773da4aa727e2b51bc5ada7e86aaa",
    },
    "subdirectory": "chessnut",
}


def test_a_branch_install_says_its_branch_and_commit(monkeypatch) -> None:
    installed(monkeypatch, GIT)
    assert build.build_info() == (
        f"palamede-chessnut {__version__}, branche feat/tablier-refused-move-signal, commit 2087b3c"
    )


def test_an_install_of_the_default_branch(monkeypatch) -> None:
    default = {**GIT, "vcs_info": {"vcs": "git", "commit_id": "bb7f45eb016ebb07"}}
    installed(monkeypatch, default)
    assert build.build_info().endswith("branche par défaut, commit bb7f45e")


def test_a_local_copy(monkeypatch) -> None:
    installed(monkeypatch, {"url": "file:///Users/x/palamede-chessboard/chessnut", "dir_info": {}})
    assert build.build_info().endswith("copie locale /Users/x/palamede-chessboard/chessnut")


@pytest.mark.parametrize("direct_url", [None, "{not json", ["a list"], {"url": "https://pypi"}])
def test_otherwise_the_release_alone(monkeypatch, direct_url) -> None:
    installed(monkeypatch, direct_url)
    assert build.build_info() == f"palamede-chessnut {__version__}"


def test_not_installed_at_all(monkeypatch) -> None:
    def missing(_name: str):
        raise metadata.PackageNotFoundError(_name)

    monkeypatch.setattr(build.metadata, "distribution", missing)
    assert build.build_info() == f"palamede-chessnut {__version__}"
