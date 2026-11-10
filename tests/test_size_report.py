"""The CI job ``size`` : the survey's four measures, warned about without blocking."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("size_report", ROOT / "scripts" / "size_report.py")
assert _spec is not None and _spec.loader is not None
size_report = importlib.util.module_from_spec(_spec)
sys.modules["size_report"] = size_report  # the dataclass resolves its annotations there
_spec.loader.exec_module(size_report)

Breach = size_report.Breach


def test_py_loc_leaves_out_blank_lines_comments_and_docstrings() -> None:
    text = '''"""Module docstring,

on two lines."""

import os  # a comment after code still counts

# a comment alone
def f():
    """One-line docstring."""
    return os
'''
    assert size_report.py_loc(text) == 3


def test_js_loc_leaves_out_comments_and_jinja_tags() -> None:
    text = """{% include "x" %}
/* a block
   comment */
// a line comment
const a = 1;

function f() { return a; }
"""
    assert size_report.js_loc(text) == 2


def test_js_functions_count_the_branches_of_their_own_body() -> None:
    text = """function alpha(a, b) {
  if (a && b) { for (const x of a) { if (x) { return x ?? b; } } }
  return a ? b : null;
}
const beta = async (x) => { try { await x; } catch (e) { return e?.message; } };
"""
    found = size_report.js_functions(text)
    assert [(name, line, branches) for name, line, branches, _lines in found] == [
        ("alpha", 1, 6),  # if, &&, for, if, ?: — not ??
        ("beta", 5, 2),  # catch — not ?.
    ]
    assert found[0][3] == 4


def _measures(**over) -> dict:
    measures = {
        "limits": {"c901": 15, "py_lines": 400, "js_lines": 300, "js_branches": 20},
        "python_files": [{"path": "a.py", "loc": 10}],
        "python_functions": [{"path": "a.py", "line": 3, "name": "f", "complexity": 2}],
        "js_templates": [{"path": "t.js.jinja", "loc": 10}],
        "js_functions": [{"path": "t.js.jinja", "line": 1, "name": "g", "branches": 2, "lines": 3}],
    }
    measures.update(over)
    return measures


def test_no_breach_under_the_thresholds() -> None:
    assert size_report.breaches(_measures()) == []
    assert "Aucun dépassement." in size_report.summary(_measures(), [])


def test_breaches_the_worst_first_on_every_measure() -> None:
    measures = _measures(
        python_files=[{"path": "big.py", "loc": 401}],
        python_functions=[{"path": "a.py", "line": 3, "name": "f", "complexity": 16}],
        js_templates=[{"path": "t.js.jinja", "loc": 350}],
        js_functions=[{"path": "t.js.jinja", "line": 9, "name": "g", "branches": 31, "lines": 3}],
    )
    found = size_report.breaches(measures)
    assert [(b.name, b.value, b.limit) for b in found] == [
        ("t.js.jinja", 350, 300),
        ("g", 31, 20),
        ("f", 16, 15),  # same excess as big.py : the path decides
        ("big.py", 401, 400),
    ]
    assert found[1].line == 9 and found[3].line == 1


def test_the_annotation_names_the_file_and_the_line() -> None:
    breach = Breach("complexité Python (C901)", "a.py", 3, "f", 16, 15)
    assert (
        size_report.annotation(breach)
        == "::warning file=a.py,line=3,title=size::complexité Python (C901) : f = 16, seuil 15"
    )
    text = size_report.summary(_measures(), [breach])
    assert (
        "1 dépassement(s)" in text and "| complexité Python (C901) | `a.py:3` f | 16 | 15 |" in text
    )


def test_the_repository_is_surveyed_with_ruff() -> None:
    measures = size_report.survey(ROOT)
    paths = {f["path"] for f in measures["python_files"]}
    assert "chessnut/palamede_chessnut/lobby.py" in paths and not any("/tests/" in p for p in paths)
    assert measures["python_functions"] and all(
        f["complexity"] >= 1 for f in measures["python_functions"]
    )
    assert {f["name"] for f in measures["js_functions"]} >= {"pollPositions", "renderClocks"}
    kinds = {b.kind for b in size_report.breaches(measures)}
    assert kinds <= {
        "complexité Python (C901)",
        "fichier Python (LOC)",
        "template JS (LOC)",
        "fonction JS (branches estimées)",
    }


def test_main_warns_under_actions_writes_the_summary_and_exits_1(
    tmp_path, monkeypatch, capsys
) -> None:
    measures = _measures(
        python_functions=[{"path": "a.py", "line": 3, "name": "f", "complexity": 16}]
    )
    monkeypatch.setattr(size_report, "survey", lambda root: measures)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    assert size_report.main([]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "::warning file=a.py,line=3,title=size::complexité Python (C901) : f = 16, seuil 15"
    ]
    assert "| `a.py:3` f | 16 | 15 |" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_main_exits_0_and_prints_the_summary_elsewhere(monkeypatch, capsys) -> None:
    monkeypatch.setattr(size_report, "survey", lambda root: _measures())
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert size_report.main([]) == 0
    assert "Aucun dépassement." in capsys.readouterr().out


def test_json_prints_every_measure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(size_report, "survey", lambda root: _measures())
    assert size_report.main(["--json"]) == 0
    assert '"python_functions"' in capsys.readouterr().out


def test_the_workflow_runs_the_job_without_blocking() -> None:
    # Parsed, not only read : a colon in a step name made the whole file invalid
    # and the run failed with no job at all (run 703, 10/11).
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["size"]
    assert job["continue-on-error"] is True
    assert any(
        step.get("run") == "poetry run python scripts/size_report.py" for step in job["steps"]
    )
