"""Validate the contract artifacts of the pharmacy engine.

Usage: .venv/bin/python scripts/validate_contracts.py

Checks (docs/ENGINE-SPEC.md section 2.4): generated schemas match pharmacy.types, every
configuration document validates and its cross-references resolve, every fixture validates
and is consistent with the configuration, every test-vector file validates with unique
vector ids, calibration vector and row references resolve, measured degradant ids resolve,
and no en or em dash appears in the files of the contract. Schema validation includes
calibration provenance, reference conditions and explicit Q3B rounding precision.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pharmacy.contracts import repository_problems  # noqa: E402

DASHES = (chr(0x2013), chr(0x2014))  # en dash, em dash
CHECKED = ("docs", "config", "contracts", "fixtures", "pharmacy", "tests", "scripts")


def dash_problems() -> list[str]:
    problems = []
    for top in CHECKED:
        for path in sorted((ROOT / top).rglob("*")):
            if path.suffix not in {".py", ".json", ".md", ".toml"} or "__pycache__" in path.parts:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if any(d in line for d in DASHES):
                    problems.append(f"{path.relative_to(ROOT)}:{number}: en or em dash")
    return problems


def main() -> int:
    problems = repository_problems(ROOT) + dash_problems()
    for problem in problems:
        print(problem)
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
