"""Stored form of a warning (E24 5.1): canonical JSON of every field, parsed back at the storage boundary.

The definition a warning was raised under travels with it (D1), written by the domain's own `definition_to_raw`
and read by `parse_definition`, so a release that no longer holds the definition cannot change a stored warning.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import cast

from crew_health.kernel.clock import require_utc
from crew_health.rules.domain.definitions import definition_to_raw, parse_definition
from crew_health.rules.domain.errors import InvariantViolation
from crew_health.rules.domain.vocabulary import (
    Basis,
    ClearReason,
    ConditionKind,
    EnvironmentId,
    EvidenceKind,
    Level,
    OriginMode,
    RaisedBy,
    Scope,
    WarningState,
)
from crew_health.rules.domain.warning import (
    Acknowledgement,
    Citation,
    DefinitionPin,
    Dismissal,
    EvidenceRef,
    HeadlineFields,
    LimitPin,
    Note,
    Resolution,
    TriggerRef,
    Warning,
)

type Raw = Mapping[str, object]


def instant(value: datetime) -> str:
    require_utc(value)
    return value.isoformat().replace("+00:00", "Z")


def parse_instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise InvariantViolation("stored instant is not text")
    parsed = datetime.fromisoformat(value)
    require_utc(parsed)
    return parsed


def _opt_instant(value: object) -> datetime | None:
    return None if value is None else parse_instant(value)


def _basis(value: object) -> Basis | None:
    return None if value is None else Basis(str(value))


def _limit_raw(pin: LimitPin | None) -> object:
    if pin is None:
        return None
    return {
        "limit_id": pin.limit_id,
        "limit_fingerprint": pin.limit_fingerprint,
        "dwell_seconds": pin.dwell_seconds,
        "stale_after_seconds": pin.stale_after_seconds,
        "basis": None if pin.basis is None else pin.basis.value,
        "clear_basis": None if pin.clear_basis is None else pin.clear_basis.value,
    }


def to_raw(w: Warning) -> dict[str, object]:
    return {
        "warning_id": w.warning_id,
        "revision": w.revision,
        "definition_id": w.definition_id,
        "config_release_id": w.config_release_id,
        "level": w.level.value,
        "raised_by": w.raised_by.value,
        "hazard_id": w.hazard_id,
        "limit_id": w.limit_id,
        "sensor_type_id": w.sensor_type_id,
        "channel_id": w.channel_id,
        "scope": w.scope.value,
        "person_id": w.person_id,
        "environment_id": w.environment_id.value,
        "location": w.location,
        "origin_mode": w.origin_mode.value,
        "title": w.title,
        "what": w.what,
        "why": w.why,
        "steps": list(w.steps),
        "body_regions": list(w.body_regions),
        "headline": {"fragment": w.headline.fragment, "rank": w.headline.rank, "solo": w.headline.solo},
        "evidence": [evidence_raw(e) for e in w.evidence],
        "evidence_time": instant(w.evidence_time),
        "evidence_time_label": w.evidence_time_label,
        "citation": {
            "text": w.citation.text,
            "source_ids": list(w.citation.source_ids),
            "basis": None if w.citation.basis is None else w.citation.basis.value,
        },
        "insight_id": w.insight_id,
        "state": w.state.value,
        "raised_at": instant(w.raised_at),
        "acknowledgements": [{"person_id": a.person_id, "at": instant(a.at)} for a in w.acknowledgements],
        "cleared_at": None if w.cleared_at is None else instant(w.cleared_at),
        "clear_reason": None if w.clear_reason is None else w.clear_reason.value,
        "resolved": None
        if w.resolved is None
        else {
            "by_id": w.resolved.by_id,
            "by_kind": w.resolved.by_kind,
            "at": instant(w.resolved.at),
            "reason": w.resolved.reason,
            "role": w.resolved.role,
        },
        "dismissed": None
        if w.dismissed is None
        else {"by_id": w.dismissed.by_id, "at": instant(w.dismissed.at), "reason": w.dismissed.reason},
        "notes": [{"by_id": n.by_id, "at": instant(n.at), "text": n.text} for n in w.notes],
        "previous_warning_id": w.previous_warning_id,
        "routine_override_ids": list(w.routine_override_ids),
        "scope_key": w.scope_key,
        "trigger": {"kind": w.trigger.kind.value, "ref": w.trigger.ref},
        "rule_fingerprint": w.rule_fingerprint,
        "content_hash": w.content_hash,
        "origin_node_id": w.origin_node_id,
        "pin": {"definition": definition_to_raw(w.pin.definition), "limit": _limit_raw(w.pin.limit)},
        "source_revision": w.source_revision,
        "source_partition": w.source_partition,
    }


def evidence_raw(e: EvidenceRef) -> dict[str, object]:
    return {
        "kind": e.kind.value,
        "ref_id": e.ref_id,
        "summary": e.summary,
        "observed_at": None if e.observed_at is None else instant(e.observed_at),
    }


def dumps(w: Warning) -> str:
    return json.dumps(to_raw(w), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _text(raw: Raw, key: str) -> str:
    value = raw[key]
    if not isinstance(value, str):
        raise InvariantViolation("stored field is not text: " + key)
    return value


def _opt_text(raw: Raw, key: str) -> str | None:
    value = raw[key]
    return None if value is None else _text(raw, key)


def _list(raw: Raw, key: str) -> list[object]:
    value = raw[key]
    if not isinstance(value, list):
        raise InvariantViolation("stored field is not a list: " + key)
    return value


def _map(value: object) -> Raw:
    if not isinstance(value, dict):
        raise InvariantViolation("stored field is not an object")
    return cast(Raw, value)


def _int(raw: Raw, key: str) -> int:
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvariantViolation("stored field is not an integer: " + key)
    return value


def _limit_pin(value: object) -> LimitPin | None:
    if value is None:
        return None
    raw = _map(value)
    return LimitPin(
        _text(raw, "limit_id"),
        _text(raw, "limit_fingerprint"),
        _int(raw, "dwell_seconds"),
        _int(raw, "stale_after_seconds"),
        _basis(raw["basis"]),
        _basis(raw["clear_basis"]),
    )


def _evidence(value: object) -> EvidenceRef:
    raw = _map(value)
    return EvidenceRef(
        EvidenceKind(_text(raw, "kind")), _text(raw, "ref_id"), _opt_text(raw, "summary"), _opt_instant(raw["observed_at"])
    )


def _resolution(value: object) -> Resolution | None:
    if value is None:
        return None
    raw = _map(value)
    return Resolution(
        _text(raw, "by_id"), _text(raw, "by_kind"), parse_instant(raw["at"]), _text(raw, "reason"), _opt_text(raw, "role")
    )


def _dismissal(value: object) -> Dismissal | None:
    if value is None:
        return None
    raw = _map(value)
    return Dismissal(_text(raw, "by_id"), parse_instant(raw["at"]), _opt_text(raw, "reason"))


def from_raw(raw: Raw) -> Warning:
    citation = _map(raw["citation"])
    headline = _map(raw["headline"])
    trigger = _map(raw["trigger"])
    pin = _map(raw["pin"])
    return Warning(
        warning_id=_text(raw, "warning_id"),
        revision=_int(raw, "revision"),
        definition_id=_text(raw, "definition_id"),
        config_release_id=_text(raw, "config_release_id"),
        level=Level(_text(raw, "level")),
        raised_by=RaisedBy(_text(raw, "raised_by")),
        hazard_id=_opt_text(raw, "hazard_id"),
        limit_id=_opt_text(raw, "limit_id"),
        sensor_type_id=_opt_text(raw, "sensor_type_id"),
        channel_id=_opt_text(raw, "channel_id"),
        scope=Scope(_text(raw, "scope")),
        person_id=_opt_text(raw, "person_id"),
        environment_id=EnvironmentId(_text(raw, "environment_id")),
        location=_opt_text(raw, "location"),
        origin_mode=OriginMode(_text(raw, "origin_mode")),
        title=_text(raw, "title"),
        what=_text(raw, "what"),
        why=_text(raw, "why"),
        steps=tuple(str(s) for s in _list(raw, "steps")),
        body_regions=tuple(str(s) for s in _list(raw, "body_regions")),
        headline=HeadlineFields(_text(headline, "fragment"), _int(headline, "rank"), headline["solo"] is True),
        evidence=tuple(_evidence(e) for e in _list(raw, "evidence")),
        evidence_time=parse_instant(raw["evidence_time"]),
        evidence_time_label=_text(raw, "evidence_time_label"),
        citation=Citation(
            _text(citation, "text"),
            tuple(str(s) for s in _list(citation, "source_ids")),
            _basis(citation["basis"]),
        ),
        insight_id=_opt_text(raw, "insight_id"),
        state=WarningState(_text(raw, "state")),
        raised_at=parse_instant(raw["raised_at"]),
        acknowledgements=tuple(
            Acknowledgement(_text(_map(a), "person_id"), parse_instant(_map(a)["at"]))
            for a in _list(raw, "acknowledgements")
        ),
        cleared_at=_opt_instant(raw["cleared_at"]),
        clear_reason=None if raw["clear_reason"] is None else ClearReason(_text(raw, "clear_reason")),
        resolved=_resolution(raw["resolved"]),
        dismissed=_dismissal(raw["dismissed"]),
        notes=tuple(
            Note(_text(_map(n), "by_id"), parse_instant(_map(n)["at"]), _text(_map(n), "text"))
            for n in _list(raw, "notes")
        ),
        previous_warning_id=_opt_text(raw, "previous_warning_id"),
        routine_override_ids=tuple(str(s) for s in _list(raw, "routine_override_ids")),
        scope_key=_text(raw, "scope_key"),
        trigger=TriggerRef(ConditionKind(_text(trigger, "kind")), _text(trigger, "ref")),
        rule_fingerprint=_text(raw, "rule_fingerprint"),
        content_hash=_text(raw, "content_hash"),
        origin_node_id=_text(raw, "origin_node_id"),
        pin=DefinitionPin(parse_definition(pin["definition"]), _limit_pin(pin["limit"])),
        source_revision=None if raw["source_revision"] is None else _int(raw, "source_revision"),
        source_partition=_opt_text(raw, "source_partition"),
    )


def loads(text: str) -> Warning:
    return from_raw(_map(json.loads(text)))
