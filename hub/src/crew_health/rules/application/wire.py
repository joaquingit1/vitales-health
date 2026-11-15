"""The wire `Warning` of the contract (warning.schema.json) built from the domain entity, and the outbox events."""

from __future__ import annotations

import json
from collections.abc import Sequence

from crew_health.contracts_gen.enums import EventType
from crew_health.contracts_gen.schemas.warning_schema import Warning as WireWarning
from crew_health.kernel.uow import Audience, AudienceProjectionKey, EventScope, OutboxEvent, UnitOfWork
from crew_health.rules.adapters.codec import evidence_raw, instant
from crew_health.rules.application.ports import WarningFact
from crew_health.rules.domain.vocabulary import Scope, WarningAction
from crew_health.rules.domain.warning import Warning

AUDIENCE_RULE = "rules.warning"
PROJECTION = AudienceProjectionKey("rules", "warning", 1)


def fact_of(w: Warning) -> WarningFact:
    return WarningFact(
        w.warning_id, w.revision, w.definition_id, w.level.value, w.scope.value, w.person_id,  # type: ignore[arg-type]
        w.environment_id.value, w.origin_mode.value, w.raised_by.value, w.hazard_id, w.state.value,  # type: ignore[arg-type]
        w.raised_at, w.cleared_at, None if w.clear_reason is None else w.clear_reason.value,
    )  # fmt: skip


def wire_dict(w: Warning, *, steps: Sequence[str], allowed: Sequence[WarningAction]) -> dict[str, object]:
    """Only contract fields. `steps` are the served steps (3.16), `allowed` the session's actions (3.10)."""
    return {
        "warning_id": w.warning_id, "revision": w.revision, "definition_id": w.definition_id,
        "config_release_id": w.config_release_id, "level": w.level.value, "raised_by": w.raised_by.value,
        "hazard_id": w.hazard_id, "limit_id": w.limit_id, "sensor_type_id": w.sensor_type_id,
        "channel_id": w.channel_id, "scope": w.scope.value, "person_id": w.person_id,
        "environment_id": w.environment_id.value, "location": w.location, "origin_mode": w.origin_mode.value,
        "title": w.title, "what": w.what, "why": w.why, "steps": list(steps),
        "body_regions": list(w.body_regions),
        "headline": {"fragment": w.headline.fragment, "rank": w.headline.rank, "solo": w.headline.solo},
        "evidence": [evidence_raw(e) for e in w.evidence], "evidence_time": instant(w.evidence_time),
        "evidence_time_label": w.evidence_time_label,
        "citation": {"text": w.citation.text, "source_ids": list(w.citation.source_ids),
                     "basis": None if w.citation.basis is None else w.citation.basis.value},
        "insight_id": w.insight_id, "state": w.state.value, "raised_at": instant(w.raised_at),
        "acknowledgements": [{"person_id": a.person_id, "at": instant(a.at)} for a in w.acknowledgements],
        "cleared_at": None if w.cleared_at is None else instant(w.cleared_at),
        "clear_reason": None if w.clear_reason is None else w.clear_reason.value,
        "resolved": None if w.resolved is None else {
            "by": {"kind": w.resolved.by_kind, "id": w.resolved.by_id}, "at": instant(w.resolved.at),
            "reason": w.resolved.reason},
        "dismissed": None if w.dismissed is None else {
            "by": {"kind": "person", "id": w.dismissed.by_id}, "at": instant(w.dismissed.at),
            "reason": w.dismissed.reason},
        "notes": [{"by": {"kind": "person", "id": n.by_id}, "at": instant(n.at), "text": n.text} for n in w.notes],
        "previous_warning_id": w.previous_warning_id, "routine_override_ids": list(w.routine_override_ids),
        "allowed_actions": [a.value for a in allowed],
    }  # fmt: skip


def wire_json(w: Warning, *, steps: Sequence[str], allowed: Sequence[WarningAction]) -> dict[str, object]:
    """Validated against the generated model, so a drift from the contract fails here and not at a client."""
    model = WireWarning.model_validate(wire_dict(w, steps=steps, allowed=allowed))
    dumped = model.model_dump(mode="json")
    return dumped


def emit(uow: UnitOfWork, kind: EventType, w: Warning) -> None:
    """Event data is the stored warning with `allowed_actions: []`: lifecycle facts, never a projection (4.4)."""
    data = json.dumps(wire_json(w, steps=w.steps, allowed=()), sort_keys=True).encode()
    object_ref = f"{w.scope.value}:{w.person_id or ''}"
    scope = EventScope(w.scope.value, w.person_id if w.scope is Scope.PERSON else None, w.environment_id.value)  # type: ignore[arg-type]
    uow.emit(
        OutboxEvent(
            kind, uow.now, scope, uow.release.release_id, uow.correlation_id, data,
            Audience(AUDIENCE_RULE, object_ref, PROJECTION, "1"),
        )
    )  # fmt: skip
