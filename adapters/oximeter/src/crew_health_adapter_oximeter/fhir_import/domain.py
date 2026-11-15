"""Pure typed clinical drafts. Source attribution never establishes connected hardware."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, cast

type Coding = tuple[str, str, str | None]


class ImportRejected(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ImportMappingError(Exception):
    def __init__(self, pointer: str, reason: str) -> None:
        self.pointer = pointer
        self.reason = reason
        super().__init__(f"{pointer}:{reason}")


@dataclass(frozen=True, slots=True)
class NumericRule:
    system: str
    code: str
    indicator_code: str
    accepted_ucum: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InterpretationRule:
    system: str
    code: str
    finding_kind: str


@dataclass(frozen=True, slots=True)
class ImportMapping:
    version: str
    provenance: tuple[tuple[str, str], ...]
    numeric: tuple[NumericRule, ...]
    interpretations: tuple[InterpretationRule, ...]


@dataclass(frozen=True, slots=True)
class NumericFinding:
    indicator_code: str
    value: Decimal
    ucum: str
    examined_at: datetime
    imported_at: datetime
    subject: str
    performer: tuple[str, ...]
    device_reference: str | None
    pointer: str
    source_sha256: str
    acquisition_method: Literal["imported_report"] = "imported_report"


@dataclass(frozen=True, slots=True)
class QualitativeFinding:
    finding_kind: str
    source_codings: tuple[Coding, ...]
    text: str | None
    examined_at: datetime
    imported_at: datetime
    subject: str
    interpreter: tuple[str, ...]
    pointer: str
    source_sha256: str
    confirmed: Literal[False] = False


@dataclass(frozen=True, slots=True)
class HeldItem:
    pointer: str
    reason: str
    codings: tuple[Coding, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportResult:
    import_id: str
    source_sha256: str
    status: Literal["awaiting_confirmation"]
    numeric: tuple[NumericFinding, ...]
    qualitative: tuple[QualitativeFinding, ...]
    held: tuple[HeldItem, ...]


def object_fields(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def array_items(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def reference(value: object) -> str | None:
    text = object_fields(value).get("reference")
    return text if isinstance(text, str) and text.strip() else None


def codings(value: object) -> tuple[Coding, ...]:
    result: list[Coding] = []
    for item in array_items(object_fields(value).get("coding")):
        row = object_fields(item)
        system, code, display = row.get("system"), row.get("code"), row.get("display")
        if isinstance(system, str) and isinstance(code, str):
            result.append((system, code, display if isinstance(display, str) else None))
    return tuple(result)


def effective_time(observation: dict[str, object]) -> datetime:
    value = observation.get("effectiveDateTime")
    if value is None:
        value = object_fields(observation.get("effectivePeriod")).get("start")
    if value is None:
        raise ImportRejected("time_missing")
    # R4 full dateTime, including seconds and an explicit timezone, never a partial date.
    pattern = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))"
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ImportRejected("time_ambiguous")
    try:
        result = datetime.fromisoformat(value)
        # Prove UTC conversion is representable, including at calendar boundaries.
        result.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise ImportRejected("time_ambiguous") from exc
    return result


def check_observation(
    observation: dict[str, object], expected_subject: str, expected_date: date | None, now: datetime
) -> datetime:
    subject = reference(observation.get("subject"))
    if subject is None:
        raise ImportRejected("subject_missing")
    if subject != expected_subject:
        raise ImportRejected("wrong_subject")
    examined = effective_time(observation)
    if examined > now:
        raise ImportRejected("future_time")
    if expected_date is not None and examined.date() != expected_date:
        raise ImportRejected("date_mismatch")
    if observation.get("status") not in ("final", "amended", "corrected"):
        raise ImportRejected("not_final")
    return examined


def performers(observation: dict[str, object]) -> tuple[str, ...]:
    return tuple(
        ref for item in array_items(observation.get("performer")) if (ref := reference(item)) is not None
    )


def numeric_finding(
    observation: dict[str, object],
    rule: NumericRule,
    examined: datetime,
    now: datetime,
    pointer: str,
    sha: str,
) -> NumericFinding:
    quantity = object_fields(observation.get("valueQuantity"))
    if "comparator" in quantity:
        raise ImportRejected("comparator_unsupported")
    value = quantity.get("value")
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ImportRejected("value_missing")
    unit = quantity.get("code")
    if quantity.get("system") != "http://unitsofmeasure.org" or unit not in rule.accepted_ucum:
        raise ImportRejected("unit_mismatch")
    return NumericFinding(
        rule.indicator_code,
        value,
        unit,
        examined,
        now,
        cast(str, reference(observation.get("subject"))),
        performers(observation),
        reference(observation.get("device")),
        pointer,
        sha,
    )


def qualitative_finding(
    observation: dict[str, object],
    rule: InterpretationRule,
    examined: datetime,
    now: datetime,
    pointer: str,
    sha: str,
    max_text_bytes: int,
) -> QualitativeFinding:
    if "valueQuantity" in observation:
        raise ImportRejected("unexpected_value_type")
    concept = object_fields(observation.get("valueCodeableConcept"))
    text = concept.get("text") if "valueCodeableConcept" in observation else observation.get("valueString")
    source_codings = codings(concept)
    if text is not None and not isinstance(text, str):
        raise ImportRejected("unexpected_value_type")
    if text is None and not source_codings:
        raise ImportRejected("value_missing")
    # Displays also contain source text; the same text budget covers all retained narrative.
    size = sum(len(display.encode("utf-8")) for _, _, display in source_codings if display is not None)
    if size + (len(text.encode("utf-8")) if text is not None else 0) > max_text_bytes:
        raise ImportRejected("text_too_long")
    return QualitativeFinding(
        rule.finding_kind,
        source_codings,
        text,
        examined,
        now,
        cast(str, reference(observation.get("subject"))),
        performers(observation),
        pointer,
        sha,
    )
