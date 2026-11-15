"""E01 cross-platform text I/O requires an explicit encoding (AGENTS 5.5)."""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "SYSTEM.6.5")]
ROOT = Path(__file__).resolve().parents[3]


def test_e01_text_reads_use_explicit_encoding() -> None:
    source = ROOT / "hub/src/crew_health"
    paths = sorted(
        path
        for name in ("kernel", "platform", "config", "clock", "events", "records", "app")
        for path in (source / name).rglob("*.py")
    ) + [source / "contracts_gen/build.py", ROOT / "scripts/validate_hub.py"]
    missing = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name not in ("read_text", "open"):
                continue
            if name == "open":
                position = 1 if isinstance(node.func, ast.Name) else 0
                mode = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "mode"),
                    node.args[position] if len(node.args) > position else ast.Constant("r"),
                )
                # Domain ports expose open(uow, segment), which is not file I/O.
                if not isinstance(mode, ast.Constant) or not isinstance(mode.value, str):
                    continue
                if "b" in mode.value:
                    continue
            if not any(keyword.arg == "encoding" for keyword in node.keywords):
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not missing, "Text I/O without encoding: " + ", ".join(missing)
