"""Size and complexity of the repository, warned about by the CI job ``size``.

The survey of 09/11 and the owner's decisions of 10/11 : four measures,
each with a threshold the job reports without blocking (``continue-on-error``).

* a Python function above :data:`MAX_C901` (ruff's C901, McCabe) ;
* a Python module above :data:`MAX_PY_LINES` lines of code, blank lines,
  comments and docstrings left out ;
* a JS template of the viewer above :data:`MAX_JS_LINES` lines of code ;
* a JS function above :data:`MAX_JS_BRANCHES`, an estimate : one plus the
  ``if``, ``for``, ``while``, ``case``, ``catch``, ``&&``, ``||`` and
  ``?:`` of its body (``function name(`` and ``const name = (…) => {``).

Run from the repository root : the breaches, as GitHub annotations under
Actions, and a summary (``$GITHUB_STEP_SUMMARY`` when set, else stdout) ;
exit status 1 when a threshold is crossed. ``--json`` prints every measure
instead, for the next survey (outillages ``palamede-size-report``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_C901 = 15
MAX_PY_LINES = 400
MAX_JS_LINES = 300
MAX_JS_BRANCHES = 20

ROOT = Path(__file__).resolve().parents[1]
#: The packages surveyed, tests and vendored code left out.
PY_GLOBS = ("palamede_chessboard/**/*.py", "chessnut/palamede_chessnut/**/*.py")
JS_GLOBS = (
    "palamede_chessboard/templates/*.js.jinja",
    "palamede_chessboard/templates/viewer/*.js.jinja",
)
_LEFT_OUT = ("/tests/", "/vendor/", "__pycache__")
#: ``?.`` and ``??`` are not branches.
_BRANCHES = re.compile(r"\b(?:if|for|while|case|catch)\b|&&|\|\||(?<!\?)\?(?![.?:])")
_JS_FUNCTION = re.compile(
    r"(?:async\s+)?function\s+(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{"
)
_C901 = re.compile(r"`(.+?)` is too complex \((\d+) > \d+\)")


@dataclass(frozen=True)
class Breach:
    kind: str
    path: str
    line: int
    name: str
    value: int
    limit: int

    def message(self) -> str:
        return f"{self.kind} : {self.name} = {self.value}, seuil {self.limit}"


def py_loc(text: str) -> int:
    """Lines of code : blank lines, comments and docstrings left out."""
    count, in_doc = 0, None
    for line in text.splitlines():
        s = line.strip()
        if in_doc:
            if in_doc in s:
                in_doc = None
            continue
        if not s or s.startswith("#"):
            continue
        quotes = s.lstrip("rRuU")[:3]  # r""" is a docstring too
        if quotes in ('"""', "'''") and s.startswith(("r", "R", "u", "U", quotes)):
            if s.count(quotes) == 1:
                in_doc = quotes
            continue
        count += 1
    return count


def js_loc(text: str) -> int:
    """Lines of code of a JS template : blank lines, comments and Jinja tags left out."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(("//", "{%"))
    )


def js_functions(text: str) -> list[tuple[str, int, int, int]]:
    """``(name, line, branches, lines)`` of each function, branches counted on its body."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    found = []
    for match in _JS_FUNCTION.finditer(text):
        start = text.find("{", match.end() - 1)
        depth, end = 0, start
        while end < len(text):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        body = text[start:end]
        name = match.group(1) or match.group(2)
        line = text.count("\n", 0, match.start()) + 1
        found.append((name, line, 1 + len(_BRANCHES.findall(body)), body.count("\n") + 1))
    return found


def _files(root: Path, globs: tuple[str, ...]) -> list[Path]:
    """The files surveyed ; ``_LEFT_OUT`` is looked for under ``root`` only, never above it."""
    paths = [p for g in globs for p in root.glob(g)]
    return sorted(
        p
        for p in paths
        if not any(part in f"/{p.relative_to(root).as_posix()}" for part in _LEFT_OUT)
    )


def c901(root: Path) -> list[dict]:
    """Every Python function with its McCabe complexity, by ruff (threshold 1)."""
    command = [
        sys.executable, "-m", "ruff", "check", "--select", "C901",
        "--config", "lint.mccabe.max-complexity=1", "--output-format", "json",
        *(g.split("/**")[0].split("/*")[0] for g in PY_GLOBS),
    ]  # fmt: skip
    out = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if out.returncode not in (0, 1):
        raise RuntimeError(f"ruff : {out.stderr.strip()}")
    functions = []
    for item in json.loads(out.stdout or "[]"):
        match = _C901.search(item["message"])
        if match is None:
            continue
        path = Path(item["filename"]).resolve().relative_to(root).as_posix()
        functions.append(
            {
                "path": path,
                "line": item["location"]["row"],
                "name": match.group(1),
                "complexity": int(match.group(2)),
            }
        )
    return sorted(functions, key=lambda f: (-f["complexity"], f["path"], f["line"]))


def survey(root: Path = ROOT) -> dict:
    """The four measures over the repository, every file and function."""
    py_files = [
        {"path": p.relative_to(root).as_posix(), "loc": py_loc(p.read_text(encoding="utf-8"))}
        for p in _files(root, PY_GLOBS)
    ]
    js_templates, js_funcs = [], []
    for p in _files(root, JS_GLOBS):
        text = p.read_text(encoding="utf-8")
        rel = p.relative_to(root).as_posix()
        js_templates.append({"path": rel, "loc": js_loc(text)})
        js_funcs += [
            {"path": rel, "line": line, "name": name, "branches": branches, "lines": lines}
            for name, line, branches, lines in js_functions(text)
        ]
    return {
        "limits": {
            "c901": MAX_C901,
            "py_lines": MAX_PY_LINES,
            "js_lines": MAX_JS_LINES,
            "js_branches": MAX_JS_BRANCHES,
        },
        "python_files": sorted(py_files, key=lambda f: -f["loc"]),
        "python_functions": c901(root),
        "js_templates": sorted(js_templates, key=lambda f: -f["loc"]),
        "js_functions": sorted(js_funcs, key=lambda f: -f["branches"]),
    }


def breaches(measures: dict) -> list[Breach]:
    """The thresholds crossed, the worst first."""
    found = [
        Breach(
            "complexité Python (C901)", f["path"], f["line"], f["name"], f["complexity"], MAX_C901
        )
        for f in measures["python_functions"]
        if f["complexity"] > MAX_C901
    ]
    found += [
        Breach("fichier Python (LOC)", f["path"], 1, f["path"], f["loc"], MAX_PY_LINES)
        for f in measures["python_files"]
        if f["loc"] > MAX_PY_LINES
    ]
    found += [
        Breach("template JS (LOC)", f["path"], 1, f["path"], f["loc"], MAX_JS_LINES)
        for f in measures["js_templates"]
        if f["loc"] > MAX_JS_LINES
    ]
    found += [
        Breach(
            "fonction JS (branches estimées)",
            f["path"],
            f["line"],
            f["name"],
            f["branches"],
            MAX_JS_BRANCHES,
        )
        for f in measures["js_functions"]
        if f["branches"] > MAX_JS_BRANCHES
    ]
    return sorted(found, key=lambda b: (-(b.value - b.limit), b.path, b.line))


def annotation(breach: Breach) -> str:
    """A GitHub Actions warning, shown on the file in the pull request."""
    return f"::warning file={breach.path},line={breach.line},title=size::{breach.message()}"


def summary(measures: dict, found: list[Breach]) -> str:
    """The run's summary, Markdown."""
    limits = measures["limits"]
    lines = [
        "## Taille et complexité",
        "",
        f"Seuils : C901 {limits['c901']}, fichier Python {limits['py_lines']} LOC, "
        f"template JS {limits['js_lines']} LOC, fonction JS {limits['js_branches']} branches. "
        f"Relevé : {len(measures['python_files'])} fichiers et {len(measures['python_functions'])} fonctions Python, "
        f"{len(measures['js_templates'])} templates et {len(measures['js_functions'])} fonctions JS.",
        "",
    ]
    if not found:
        lines.append("Aucun dépassement.")
        return "\n".join(lines) + "\n"
    lines += [
        f"{len(found)} dépassement(s) :",
        "",
        "| mesure | où | valeur | seuil |",
        "|---|---|---|---|",
        *(f"| {b.kind} | `{b.path}:{b.line}` {b.name} | {b.value} | {b.limit} |" for b in found),
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="repository root (default : this checkout)"
    )
    parser.add_argument(
        "--json", action="store_true", help="print every measure as JSON instead of the breaches"
    )
    args = parser.parse_args(argv)
    measures = survey(args.root.resolve())
    if args.json:
        print(json.dumps(measures, indent=2, ensure_ascii=False))
        return 0
    found = breaches(measures)
    for breach in found:
        print(annotation(breach) if os.environ.get("GITHUB_ACTIONS") else breach.message())
    text = summary(measures, found)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text, end="")
    return 1 if found else 0


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # a closed pipe ends it quietly
    sys.exit(main())
