"""Records' observation policies (adapters/policies.py, R-E02-31 and the R-E01-1 interim): bound to the pinned
release's unit mapping, absent for anything the policy document does not cover."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.platform.adapters.units import UcumUnitRegistry
from crew_health.records.adapters.policies import ReleaseObservationPolicies
from crew_health.records.domain.errors import PolicyMissing, RecordError

pytestmark = pytest.mark.trace("R-E02-31", "T-E01-06")

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class _Document:
    raw: bytes


@dataclass(frozen=True)
class _Release:
    mapping: bytes

    def document(self, name: ConfigName) -> _Document:
        if name != ConfigName.UNIT_MAPPING:
            raise KeyError(name)
        return _Document(self.mapping)


def _release(edit: dict[str, str] | None = None) -> _Release:
    mapping = json.loads((ROOT / "config/unit-mapping.json").read_text(encoding="utf-8"))
    for row in mapping["mappings"]:
        if edit is not None and row["indicator_code"] == "pulse":
            row.update(edit)
    return _Release(json.dumps(mapping).encode())


def test_camera_pulse_is_bound_to_the_release_unit_mapping_and_other_codes_have_no_policy() -> None:
    policies = ReleaseObservationPolicies.packaged(UcumUnitRegistry(max_cached=16))
    release = _release()
    policy = policies.for_code(release, "pulse")  # type: ignore[arg-type]
    mapping = json.loads(release.mapping)
    assert policy.version.endswith("+" + mapping["version"])
    assert policy.accepted_wire_units == frozenset({"bpm"})
    assert policy.methods == frozenset({"camera_estimate"})
    assert (policy.minimum, policy.maximum) == (Decimal(42), Decimal(180))
    with pytest.raises(PolicyMissing):
        policies.for_code(release, "spo2")  # type: ignore[arg-type]
    # A release that maps pulse to another unit leaves the camera bounds without a policy, never converted.
    with pytest.raises(PolicyMissing):
        policies.for_code(_release({"ucum_unit": "/s"}), "pulse")  # type: ignore[arg-type]


def test_a_policy_document_without_a_basis_or_with_inverted_bounds_never_starts() -> None:
    units = UcumUnitRegistry(max_cached=16)
    raw = json.loads((ROOT / "hub/src/crew_health/records/schemas/observation-policies.json").read_bytes())
    unsourced = json.loads(json.dumps(raw))
    unsourced["policies"][0]["minimum"]["basis"] = ""
    inverted = json.loads(json.dumps(raw))
    inverted["policies"][0]["minimum"]["value"] = "200"
    for document in (unsourced, inverted):
        with pytest.raises(RecordError):
            ReleaseObservationPolicies(units, json.dumps(document).encode())
