"""Domain to wire mapping for MeasurementRequest, Capability and CapabilityGap (closed contract schemas)."""

from __future__ import annotations

from crew_health.measurement.application.ports import CapabilityView
from crew_health.measurement.application.wire import format_instant
from crew_health.measurement.domain.model import (
    CapabilityGap,
    KitCheck,
    MeasurementRequest,
    Viewer,
    allowed_actions,
)


def kit_check_wire(kit: KitCheck) -> dict[str, object]:
    return {
        "can": kit.can,
        "instrument_ids": list(kit.instrument_ids),
        "missing_instrument_ids": list(kit.missing_instrument_ids),
        "condition_ids": list(kit.condition_ids),
        "cliff_urls": list(kit.cliff_urls),
        "text": kit.text,
        "kit_map_version": kit.kit_map_version,
        "environment_id": kit.environment_id,
    }


def request_wire(request: MeasurementRequest, viewer: Viewer | None) -> dict[str, object]:
    """viewer None renders for an audience without actions (event payloads, adapters): allowed_actions empty."""
    actions = [] if viewer is None else list(allowed_actions(request, viewer))
    return {
        "request_id": request.request_id,
        "revision": request.revision,
        "episode_id": request.episode_id,
        "capability_code": request.capability_code,
        "reason": request.reason,
        "decision_affected": request.decision_affected,
        "kit_check": kit_check_wire(request.kit_check),
        "requester": {
            "kind": request.requester.kind,
            "id": request.requester.id,
            "display_name": request.requester.display_name,
        },
        "operator_id": request.operator_id,
        "consent_ref": request.consent_ref,
        "state": request.state,
        "attempts": [
            {
                "attempt_id": a.attempt_id,
                "sequence": a.sequence,
                "adapter_id": a.adapter_id,
                "started_at": format_instant(a.started_at),
                "ended_at": None if a.ended_at is None else format_instant(a.ended_at),
                "outcome": a.outcome,
                "failure_reason": a.failure_reason,
                "observation_ids": list(a.observation_ids),
                "quality_notes": list(a.quality_notes),
            }
            for a in request.attempts
        ],
        "result_observation_ids": list(request.result_observation_ids),
        "body_region": request.body_region,
        "created_at": format_instant(request.created_at),
        "allowed_actions": actions,
    }


def capability_wire(capability: CapabilityView) -> dict[str, object]:
    return {
        "code": capability.code,
        "label": capability.label,
        "methods": list(capability.methods),
        "device_ids": list(capability.device_ids),
        "instrument_ids": list(capability.instrument_ids),
        "missing_instrument_ids": list(capability.missing_instrument_ids),
        "operator_requirement": capability.operator_requirement,
        "applicable_environments": list(capability.applicable_environments),
        "status": capability.status,
        "unavailable_reason": capability.unavailable_reason,
        "expected_duration_seconds": capability.expected_duration_seconds,
        "body_region": capability.body_region,
    }


def gap_wire(gap: CapabilityGap) -> dict[str, object]:
    return {
        "gap_id": gap.gap_id,
        "episode_id": gap.episode_id,
        "capability_code": gap.capability_code,
        "reason": gap.reason,
        "consequence": gap.consequence,
        "alternatives": list(gap.alternatives),
        "body_region": gap.body_region,
        "state": gap.state,
        "plan_id": gap.plan_id,
        "check_another_way_step_ids": list(gap.check_another_way_step_ids),
    }
