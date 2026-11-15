"""Station resources for the tests that start a hub all the way to a ready health.

The repository's own first-start release is refused today (test_serve.py pins it). config/kit-map.json fails loader
check 1 (D22's ICL36A/ICL36B suffix awaits the contract regeneration), and the configured documents disagree with
each other. config/onboard-plans.json (E28) scopes plans that config/kit-map.json does not reference, and
the plans release check verifies no form quote or rtdc, so it refuses every one.

These tests therefore start from a copy of the repository resources whose config/ pairs the package's own kit-map
and onboard-plans examples, which agree with each other. In the plans example, every form quote and the one rtdc
are set to null, as most of its steps already are. The documents that need no change come from config/ unchanged."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _example(name: str) -> object:
    return json.loads((ROOT / "contracts/api/examples/config" / (name + ".example.json")).read_bytes())[
        "value"
    ]


def copy_station_resources(target: Path) -> Path:
    """The repository resources a hub reads (contracts/, config/, docs/product/, docs/requests/) copied to `target`,
    with config/kit-map.json and config/onboard-plans.json written as described above."""
    for name in ("contracts", "config", "docs/product", "docs/requests"):
        shutil.copytree(ROOT / name, target / name)
    kit_map = json.loads((ROOT / "contracts/api/examples/kit-map.example.json").read_bytes())
    plans = _example("onboard-plans")
    assert isinstance(plans, dict)
    for plan in plans["plans"]:
        for stage in plan["stages"]:
            if stage.get("rtdc") is not None:
                stage["rtdc"] = None
            for step in stage.get("steps", []):
                step["replaces_ground_task"] = None
    for name, value in (("kit-map", kit_map), ("onboard-plans", plans)):
        (target / "config" / (name + ".json")).write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )
    return target


@pytest.fixture
def copy_resources() -> Callable[[Path], Path]:
    """`copy_station_resources`, for a test that edits its own copy."""
    return copy_station_resources


@pytest.fixture(scope="session")
def station_resources(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_station_resources(tmp_path_factory.mktemp("station") / "resources")
