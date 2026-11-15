"""T-E18-03/04/07/17/31: actual case/partition/scan CLI with a temporary owner identity."""

import json
import shutil
from pathlib import Path

import pytest
from crew_health_bench.api.cli import run
from pyrage import x25519

pytestmark = [pytest.mark.e18, pytest.mark.trace("R23", "T-E18-07", "T-E18-17", "T-E18-31")]
ROOT = Path(__file__).parents[1]


def workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    repository = tmp_path / "repository"
    bench = repository / "eval/bench"
    shutil.copytree(ROOT / "config", bench / "config")
    shutil.copytree(ROOT / "schemas", bench / "schemas")
    working = tmp_path / "working"
    working.mkdir()
    case = json.loads((ROOT / "fixtures/case-document.json").read_bytes())
    case_path = working / "fixture.case.json"
    case_path.write_text(json.dumps(case), encoding="utf-8")
    hidden = working / (case["case_id"] + ".hidden.json")
    shutil.copyfile(ROOT / "fixtures/hidden-part.json", hidden)
    return bench / "config", case_path, working


def test_case_partition_and_recursive_scan_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    config, case_path, working = workspace(tmp_path)
    identity = x25519.Identity.generate()
    key = working / "identity"
    key.write_text(str(identity), encoding="utf-8")
    monkeypatch.setenv("CHC_EVAL_IDENTITY", str(key))
    policy = json.loads((config / "partition-policy.json").read_bytes())
    policy["vault"]["recipients"] = [str(identity.to_public())]
    (config / "partition-policy.json").write_text(json.dumps(policy), encoding="utf-8")
    prefix = ["--config-dir", str(config)]
    assert run([*prefix, "cases", "validate", str(case_path), "--hidden-dir", str(working)]) == 0
    salt = working / "salt"
    salt.write_bytes(b"s" * 32)
    assert run([*prefix, "partitions", "freeze", "--cases", str(case_path), "--hidden-dir", str(working),
                "--salt", str(salt), "--fp-salt", "public"]) == 0
    assert run([*prefix, "partitions", "verify"]) == 0
    targets = working / "prompts"
    targets.mkdir()
    canary = json.loads(case_path.read_bytes())["canary"]
    (targets / "prompt.txt").write_text(canary, encoding="utf-8")
    assert run([*prefix, "leak", "scan", "--fingerprints", str(config.parent / "fingerprints/holdout.fp.json"),
                "--target", "prompts", str(targets)]) == 3
    output = capsys.readouterr()
    assert str(identity) not in output.out + output.err
    assert canary not in output.out + output.err


def test_unassigned_owner_refuses_freezing_and_configuration_derived_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, case_path, working = workspace(tmp_path)
    monkeypatch.delenv("CHC_EVAL_IDENTITY", raising=False)
    salt = working / "salt"
    salt.write_bytes(b"s" * 32)
    assert run(["--config-dir", str(config), "partitions", "freeze", "--cases", str(case_path),
                "--hidden-dir", str(working), "--salt", str(salt), "--fp-salt", "public"]) == 5
    assert not (config.parent / "partitions/FROZEN.sha256").exists()
