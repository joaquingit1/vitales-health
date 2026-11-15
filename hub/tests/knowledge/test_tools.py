import json
import subprocess
import sys
from pathlib import Path

import pytest


def scratch_inputs(tmp_path: Path) -> Path:
    manifest = json.loads(Path("knowledge/inputs/manifest.json").read_bytes())
    names = [row["source"] for row in manifest["files"]]
    names.extend(
        (
            "knowledge/inputs/manifest.json",
            "knowledge/inputs/inventories.json",
            "knowledge/policy/release-policy.json",
        )
    )
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(name).read_bytes())
    return tmp_path


def run_script(name: str, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(Path("knowledge/scripts") / name), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


@pytest.mark.trace("T-E10-39", "I1", "I6")
def test_sync_checks_all_pins_before_writing_and_rejects_conflicting_registry(tmp_path: Path) -> None:
    root = scratch_inputs(tmp_path)
    result = run_script("sync_inputs.py", root)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((root / "knowledge/inputs/manifest.json").read_bytes())
    for row in manifest["files"]:
        assert (root / row["source"]).read_bytes() == (root / row["destination"]).read_bytes()
    generated = run_script("build_registry.py", root)
    assert generated.returncode == 1
    assert json.loads(generated.stdout)["code"] == "CONFIG_INVALID"
    assert not (root / "knowledge/registry/registry.json").exists()


@pytest.mark.trace("T-E10-39", "I1", "I6")
def test_sync_rejects_corrupt_source_without_overwriting_existing_copy(tmp_path: Path) -> None:
    root = scratch_inputs(tmp_path)
    assert run_script("sync_inputs.py", root).returncode == 0
    manifest = json.loads((root / "knowledge/inputs/manifest.json").read_bytes())
    entry = manifest["files"][0]
    original = (root / entry["destination"]).read_bytes()
    (root / entry["source"]).write_bytes(b"{}")
    result = run_script("sync_inputs.py", root)
    assert result.returncode == 1
    assert json.loads(result.stdout)["code"] == "CONFIG_INVALID"
    assert (root / entry["destination"]).read_bytes() == original
