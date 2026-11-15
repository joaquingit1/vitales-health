"""T-E18-03, 09, 17: real CLI boundaries, no network or hub mocks."""

import json
from pathlib import Path

import pytest
from crew_health_bench.api.cli import run

pytestmark = [pytest.mark.e18, pytest.mark.trace("R23", "R30")]
CONFIG = Path(__file__).parents[1] / "config"


def test_t_e18_09_cli_claims(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = tmp_path / "claim.txt"
    file.write_text("HealthBench diagnostic accuracy 62%")
    assert run(["--config-dir", str(CONFIG), "lint", "claims", str(file)]) == 3
    output = capsys.readouterr()
    assert json.loads(output.out)["findings"]
    assert "62%" not in output.out
    file.write_text("engineering evaluation")
    assert run(["--config-dir", str(CONFIG), "lint", "claims", str(file)]) == 0


def test_t_e18_17_cli_refuses_missing_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["--config-dir", str(tmp_path), "lint", "claims", "missing.txt"]) == 6
    problem = json.loads(capsys.readouterr().err)
    assert problem["type"] == "urn:crew-health:bench:target_unreadable"


def test_vault_commands_use_configured_recipients_and_verify_ciphertext(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import shutil

    from pyrage import x25519

    config = tmp_path / "repository/eval/bench/config"
    shutil.copytree(CONFIG, config)
    identity = x25519.Identity.generate()
    policy_path = config / "partition-policy.json"
    policy = json.loads(policy_path.read_bytes())
    policy["vault"]["recipients"] = [str(identity.to_public())]
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    source = tmp_path / "case.json"
    source.write_text('{"canary":"private synthetic canary"}', encoding="utf-8")
    assert run(["--config-dir", str(config), "vault", "seal", str(source)]) == 0
    assert "private synthetic canary" not in capsys.readouterr().out
    assert run(["--config-dir", str(config), "vault", "verify", "case"]) == 0
    policy["vault"]["recipients"] = []
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    assert run(["--config-dir", str(config), "vault", "seal", str(source)]) == 5
