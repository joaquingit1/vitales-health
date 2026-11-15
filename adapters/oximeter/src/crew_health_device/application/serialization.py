"""Canonical contract JSON, preserving Decimal text without a float conversion."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal

from crew_health.contracts_gen.schemas.requests_schema import AttemptCreate
from crew_health_device.domain.acquisition import AttemptDraft, Binding, ObservationDraft


def _text(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _instant(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("utc_aware_instant_required")
    return _text(value.astimezone(UTC).isoformat().replace("+00:00", "Z"))


def _object(fields: dict[str, str]) -> str:
    return "{" + ",".join(f"{_text(key)}:{fields[key]}" for key in sorted(fields)) + "}"


def _number(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("finite_decimal_required")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if Decimal(text) == 0 else text


def _observation(observation: ObservationDraft) -> str:
    fields = {
        "patient_id": _text(observation.patient_id),
        "code": _text(observation.code),
        "value": _number(observation.value),
        "unit": _text(observation.unit),
        "observed_at": _instant(observation.observed_at),
        "origin_mode": _text(observation.origin_mode),
        "acquisition_method": _text(observation.acquisition_method),
        "device_id": _text(observation.device_id),
        "context": _object({key: _text(value) for key, value in observation.context}),
        "measurement_request_id": _text(observation.measurement_request_id),
    }
    if observation.episode_id is not None:
        fields["episode_id"] = _text(observation.episode_id)
    return _object(fields)


def attempt_body(draft: AttemptDraft) -> bytes:
    fields = {
        "sequence": str(draft.sequence),
        "started_at": _instant(draft.started_at),
        "ended_at": _instant(draft.ended_at),
        "outcome": _text(draft.outcome),
        "observations": "[" + ",".join(_observation(item) for item in draft.observations) + "]",
        "quality_notes": "[" + ",".join(_text(note) for note in draft.quality_notes) + "]",
    }
    if draft.failure_reason is not None:
        fields["failure_reason"] = _text(draft.failure_reason)
    body = _object(fields).encode("utf-8")
    # Validation does not supply the encoded bytes; the generated model uses floats internally.
    AttemptCreate.model_validate(json.loads(body, parse_float=Decimal))
    return body


def binding_body(binding: Binding, profile_ref: str) -> bytes:
    fields = asdict(binding)
    fields["adapter_profile"] = profile_ref
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
