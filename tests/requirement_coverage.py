#!/usr/bin/env python3
"""Generate/check the acceptance-test map from pytest function docstrings."""

import argparse
import ast
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TESTS = ROOT / "tests"
OUTPUT = ROOT / "Docs/emonos-test-coverage.md"
ACCEPTANCE = {
    "T1": ("HW-3", "Build both targets from the shared tree"),
    "T2": ("ARCH-4", "Boot and serve emoncms"),
    "T3": ("DATA-2", "Feed survives reboot"),
    "T4": ("OS-1, OS-2", "RAUC slot reporting and signed bundle verification"),
    "T5": ("OS-11, OS-13, OS-15", "Install v2, boot B, retain data"),
    "T6": ("HC-4, OS-8, HC-9", "Broken v2 autonomously rolls back"),
    "T7": ("OS-14", "Interrupted install preserves prior slot"),
    "T8": ("TEST-7", "One runner selects either target"),
}
TOKEN = re.compile(r"\b(?:T[1-8]|[A-Z]{2,5}-[0-9]+)\b")


def collect() -> dict[str, list[str]]:
    cases: dict[str, list[str]] = {}
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
                continue
            for tag in sorted(set(TOKEN.findall(ast.get_docstring(node) or ""))):
                cases.setdefault(tag, []).append(f"`{path.name}::{node.name}`")
    return cases


def render(cases: dict[str, list[str]]) -> str:
    rows = [
        "# Test requirement coverage", "",
        "Generated from `test_*` pytest function docstrings by `tests/requirement_coverage.py`.",
        "Tags on helper functions are intentionally excluded: every listed case is collected by pytest.",
        "", "| Acceptance | Proves | Collected pytest cases |", "|---|---|---|",
    ]
    missing = []
    for acceptance, (proof, description) in ACCEPTANCE.items():
        tags = [acceptance, *proof.split(", ")]
        mapped = sorted({case for tag in tags for case in cases.get(tag, [])})
        missing.extend(tag for tag in tags if not cases.get(tag))
        rows.append(f"| **{acceptance}** ({proof}) | {description} | {', '.join(mapped) or '—'} |")
    rows.extend(["", "## Additional tagged coverage", "", "| ID | Collected pytest cases |", "|---|---|"])
    excluded = {*ACCEPTANCE, *(tag for proof, _ in ACCEPTANCE.values() for tag in proof.split(", "))}
    rows.extend(f"| `{tag}` | {', '.join(sorted(set(cases[tag])))} |"
                for tag in sorted(cases) if tag not in excluded)
    if missing:
        raise SystemExit("Acceptance tags missing from test docstrings: " + ", ".join(sorted(set(missing))))
    return "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the committed table is stale")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    options = parser.parse_args()
    generated = render(collect())
    if options.check:
        if not options.output.is_file() or options.output.read_text(encoding="utf-8") != generated:
            print(f"{options.output} is stale; run python3 tests/requirement_coverage.py")
            return 1
    else:
        options.output.write_text(generated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
