"""Bounded FHIR R4 4.0.1 JSON ingress, producing local drafts only."""

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Protocol

from .domain import (
    HeldItem,
    ImportMapping,
    ImportRejected,
    ImportResult,
    NumericFinding,
    QualitativeFinding,
    check_observation,
    codings,
    numeric_finding,
    object_fields,
    qualitative_finding,
)
from .mapping import parse_mapping
from .settings import ImportSettings


class Clock(Protocol):
    def now(self) -> datetime: ...


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ImportRejected("malformed")
        result[key] = value
    return result


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ImportRejected("nonfinite") from exc
    return result


def _no_constant(value: str) -> object:
    raise ImportRejected("nonfinite")


def _check_depth(text: str, maximum: int) -> None:
    # Preflight before json.loads so the decoder's recursive descent never sees deep input.
    stack: list[str] = []
    quoted = escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            stack.append(character)
            if len(stack) > maximum:
                raise ImportRejected("too_deep")
        elif character in "]}" and stack:
            stack.pop()


def _check_strings(document: object) -> None:
    stack = [document]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            item.encode("utf-8")
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _read(raw: bytes, settings: ImportSettings) -> dict[str, object]:
    if len(raw) > settings.max_bytes:
        raise ImportRejected("too_large")
    try:
        text = raw.decode("utf-8")
        _check_depth(text, settings.max_depth)
        document: object = json.loads(
            text,
            parse_float=_decimal,
            parse_int=_decimal,
            parse_constant=_no_constant,
            object_pairs_hook=_unique_fields,
        )
        _check_strings(document)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ImportRejected("malformed") from exc
    if not isinstance(document, dict):
        raise ImportRejected("unsupported_resource")
    return object_fields(document)


def _resources(document: dict[str, object], maximum: int) -> tuple[tuple[str, object], ...]:
    kind = document.get("resourceType")
    if kind == "Observation":
        return (("", document),)
    if kind != "Bundle":
        raise ImportRejected("unsupported_resource")
    entries = document.get("entry", [])
    if not isinstance(entries, list):
        raise ImportRejected("malformed")
    if len(entries) > maximum:
        raise ImportRejected("too_many_entries")
    return tuple(
        (f"/entry/{index}/resource", object_fields(entry).get("resource"))
        for index, entry in enumerate(entries)
    )


def _finding(
    observation: dict[str, object],
    mapping: ImportMapping,
    examined: datetime,
    now: datetime,
    pointer: str,
    sha: str,
    text_limit: int,
) -> NumericFinding | QualitativeFinding:
    identities = {(system, code) for system, code, _ in codings(observation.get("code"))}
    numeric = [row for row in mapping.numeric if (row.system, row.code) in identities]
    qualitative = [row for row in mapping.interpretations if (row.system, row.code) in identities]
    # Multiple translations of one code may agree. Conflicting types/targets are held, never guessed.
    targets = {("numeric", row.indicator_code, row.accepted_ucum) for row in numeric}
    targets.update(("qualitative", row.finding_kind, ()) for row in qualitative)
    if len(targets) > 1:
        raise ImportRejected("ambiguous_code")
    if numeric:
        return numeric_finding(observation, numeric[0], examined, now, pointer, sha)
    if qualitative:
        return qualitative_finding(observation, qualitative[0], examined, now, pointer, sha, text_limit)
    raise ImportRejected("unmapped_code")


class ImportService:
    def __init__(
        self, mapping: Mapping[str, object] | ImportMapping, settings: ImportSettings, clock: Clock
    ) -> None:
        self._mapping = parse_mapping(mapping)
        self._settings = settings
        self._clock = clock
        # FIFO result cache bounded by import.max_entries. Cache hits retain the original result.
        self._results: dict[tuple[str, date | None], ImportResult] = {}

    def import_file(
        self, raw: bytes, *, expected_patient_reference: str, expected_examination_date: date | None
    ) -> ImportResult:
        if not expected_patient_reference.strip():
            raise ImportRejected("subject_missing")
        document = _read(raw, self._settings)
        resources = _resources(document, self._settings.max_entries)
        source_hash = sha256(raw).hexdigest()
        import_id = (
            "imp_" + sha256((source_hash + expected_patient_reference).encode("utf-8")).hexdigest()[:32]
        )
        key = (import_id, expected_examination_date)
        if key in self._results:
            return self._results[key]
        now = self._clock.now()
        if now.utcoffset() is None:
            raise ImportRejected("invalid_clock")
        result = self._build_result(
            resources, expected_patient_reference, expected_examination_date, now, import_id, source_hash
        )
        if len(self._results) >= self._settings.max_entries:
            del self._results[next(iter(self._results))]
        self._results[key] = result
        return result

    def _build_result(
        self,
        resources: tuple[tuple[str, object], ...],
        expected_patient_reference: str,
        expected_examination_date: date | None,
        now: datetime,
        import_id: str,
        source_hash: str,
    ) -> ImportResult:
        numeric: list[NumericFinding] = []
        qualitative: list[QualitativeFinding] = []
        held: list[HeldItem] = []
        # _resources enumerates array indexes in numeric pointer order, including indexes above nine.
        for pointer, resource in resources:
            observation = object_fields(resource)
            try:
                if observation.get("resourceType") != "Observation":
                    raise ImportRejected("unsupported_resource")
                examined = check_observation(
                    observation, expected_patient_reference, expected_examination_date, now
                )
                finding = _finding(
                    observation,
                    self._mapping,
                    examined,
                    now,
                    pointer,
                    source_hash,
                    self._settings.max_text_bytes,
                )
                if isinstance(finding, NumericFinding):
                    numeric.append(finding)
                else:
                    qualitative.append(finding)
            except ImportRejected as exc:
                held.append(HeldItem(pointer, exc.reason, codings(observation.get("code"))))
        # P-E07-2: generated typed finding/confirmation boundary is unpublished. Never post drafts.
        return ImportResult(
            import_id, source_hash, "awaiting_confirmation", tuple(numeric), tuple(qualitative), tuple(held)
        )
