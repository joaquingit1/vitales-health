"""config/onboard-plans.json: schema, reproducible build and the authoring acceptance (E28 8.3, T28-40, T28-41)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import jsonschema
import pytest
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config/onboard-plans.json"
SCHEMAS = ROOT / "contracts/api/schemas"
CONTRACT_PATTERN = "^ICL[1-9][0-9]{0,2}$"
D22_PATTERN = "^ICL[1-9][0-9]{0,2}[A-Z]?$"


def d22_schema(path: Path) -> dict[str, object]:
    """The package schema with only the condition ID pattern widened, in memory (coordinator decision D22);
    contracts/ is not edited."""
    text = path.read_text(encoding="utf-8")
    widened = text.replace(json.dumps(CONTRACT_PATTERN)[1:-1], json.dumps(D22_PATTERN)[1:-1])
    return cast(dict[str, object], json.loads(widened))


@pytest.mark.trace("T28-41", "R47", "EI3")
def test_authored_onboard_plans_validate_against_their_schema() -> None:
    registry: Registry = Registry().with_resources(
        (path.name, Resource.from_contents(d22_schema(path))) for path in SCHEMAS.glob("*.schema.json")
    )
    assert CONTRACT_PATTERN in (SCHEMAS / "config-onboard-plans.schema.json").read_text(encoding="utf-8")
    validator = jsonschema.Draft202012Validator(
        d22_schema(SCHEMAS / "config-onboard-plans.schema.json"), registry=registry
    )
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert sorted(str(e) for e in validator.iter_errors(document)) == []
    assert document["example"] is False
    plans = document["plans"]
    assert len([plan for plan in plans if "condition_id" in plan["scope"]]) == 119
    flows = json.loads((ROOT / "config/responder-flows.json").read_text(encoding="utf-8"))["flows"]
    assert {plan["scope"]["flow_id"] for plan in plans if "flow_id" in plan["scope"]} == {
        flow["id"] for flow in flows
    }
    assert len(plans) == 119 + len(flows)


@pytest.mark.trace("T28-40")
def test_build_reproduces_the_config_byte_for_byte() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "authoring/plans/build.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.trace("T28-41", "T28-20", "EI3", "R47", "R57", "V82")
def test_every_kit_map_row_has_a_plan_aboard_in_every_environment() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "authoring/plans/check.py")],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "aboard in all environments 119 of 119" in result.stdout
    assert "E28-03 gate: not done" in result.stdout
