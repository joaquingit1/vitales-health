"""T-E01-01 SYSTEM 6.5 generation does not depend on where the temporary directory lives."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "SYSTEM-6.5")]
ROOT = Path(__file__).resolve().parents[3]
COMMITTED = ROOT / "hub/src/crew_health/contracts_gen"


def _generate(temporary: Path, output: Path) -> dict[str, bytes]:
    temporary.mkdir(parents=True)
    output.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "crew_health.contracts_gen.build", "--output", str(output)],
        cwd=ROOT,
        env={**os.environ, "TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary)},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()}


def _without_modules(raw: bytes) -> dict[str, object]:
    # The module inventory hashes the hub's own sources, which other work edits; it is not generated output.
    manifest: dict[str, object] = json.loads(raw)
    manifest.pop("modules")
    return manifest


def test_python_generation_is_identical_for_any_temporary_directory(tmp_path: Path) -> None:
    # A temporary directory inside a Python project once changed the builtin formatter's line length.
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.ruff]\nline-length = 110\n", encoding="utf-8")
    plain = _generate(tmp_path / "plain", tmp_path / "plain-output")
    nested = _generate(project / "deep/temporary", tmp_path / "nested-output")
    assert plain == nested
    generated = {name: raw for name, raw in plain.items() if name != "manifest.json"}
    assert generated == {name: (COMMITTED / name).read_bytes() for name in generated}
    assert _without_modules(plain["manifest.json"]) == _without_modules(
        (COMMITTED / "manifest.json").read_bytes()
    )
