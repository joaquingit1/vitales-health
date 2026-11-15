"""The AttemptCreate body (contracts requests.schema.json AttemptCreate) for one measurement acquisition.

A selection is a success carrying every qualified view as its own observation with its own camera, the selected one
first and marked, plus the choice, its reason and any disagreement (R11, V07). A refusal is a failed attempt with the
configured reason and no observation at all: nothing is fabricated (AGENTS rule 5, D08). Words come from the policy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from crew_health_camera.application.model import Outcome, StartSpec
from crew_health_camera.domain.analysis import Reading
from crew_health_camera.domain.policy import QualityPolicy
from crew_health_camera.domain.selection import Refusal, Selection

CAMERA_METHOD = "camera_estimate"  # contracts common.schema.json acquisition_method for a camera estimate


def instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _notes(policy: QualityPolicy, outcome: Outcome) -> list[str]:
    names = dict(outcome.camera_names)
    result = outcome.result
    notes: list[str] = []
    if isinstance(result, Selection):
        views = 1 + len(result.others)
        selected = names.get(result.selected.device_id, result.selected.device_id)
        notes.append(policy.notes["selected"].format(device=selected, views=views))
        if result.disagree:
            notes.append(policy.notes["disagree"].format(spread=result.spread, unit=result.selected.unit))
    else:
        notes.append(policy.reasons[result.reason])
    for refusal in result.refused:
        device = names.get(refusal.device_id, refusal.device_id)
        notes.append(policy.notes["refused"].format(device=device, reason=policy.reasons[refusal.reason]))
    return notes


def _observation(
    spec: StartSpec, outcome: Outcome, selection: Selection, reading: Reading, policy: QualityPolicy
) -> dict[str, object]:
    names = dict(outcome.camera_names)
    return {
        "patient_id": spec.patient_id,
        "code": reading.code,
        "value": number(reading.value),
        "unit": reading.unit,
        "observed_at": instant(outcome.ended_at),
        "origin_mode": outcome.origin_mode,
        "acquisition_method": CAMERA_METHOD,
        "device_id": reading.device_id,
        "measurement_request_id": spec.request_id,
        "context": {
            "camera_name": names.get(reading.device_id, reading.device_id),
            "camera_selection": "selected" if reading is selection.selected else "also_qualified",
            "selection_reason": selection.reason,
            "qualified_views": str(1 + len(selection.others)),
            "views_disagree": "true" if selection.disagree else "false",
            "views_spread": f"{selection.spread} {reading.unit}",
            "quality_rule": f"{policy.policy_id}@{policy.version}",
        },
    }


def attempt_body(spec: StartSpec, outcome: Outcome, policy: QualityPolicy) -> dict[str, object]:
    if spec.sequence is None or spec.patient_id is None or spec.request_id is None:
        raise ValueError("measurement acquisition without request identity")
    body: dict[str, object] = {
        "sequence": spec.sequence,
        "started_at": instant(outcome.started_at),
        "ended_at": instant(outcome.ended_at),
        "quality_notes": _notes(policy, outcome),
    }
    result = outcome.result
    if isinstance(result, Refusal):
        body.update(outcome="failed", failure_reason=policy.reasons[result.reason], observations=[])
        return body
    readings = (result.selected, *result.others)
    body.update(
        outcome="success",
        observations=[_observation(spec, outcome, result, r, policy) for r in readings],
    )
    return body
