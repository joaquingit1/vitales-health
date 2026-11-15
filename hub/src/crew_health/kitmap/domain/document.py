"""Parse and check kit-map.json (E27 5.2, 8.2): raw bytes to a KitMapDoc, or every finding with its pointer.

Checks: B (bounds, first), 11a/11b/11c (CONTRACTS 11 check 11), 12 (a reviewed row names checked_by) and CAM (no
confirming test names a camera_estimate instrument; a safety invariant in code, never a setting).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from crew_health.kitmap.domain.model import (
    CAMERA_ESTIMATE,
    CapabilityWords,
    ConditionRow,
    ConfirmingTest,
    DocumentInvalid,
    Finding,
    Instrument,
    KitMapDoc,
    Operator,
    SupplyDef,
)

type Json = dict[str, object]


@dataclass(frozen=True, slots=True)
class DocumentBounds:
    """Hub settings kitmap.* of E27 section 11 (SYSTEM 5.11)."""

    max_document_bytes: int
    max_conditions: int
    max_tests_per_row: int
    max_instruments_per_test: int
    max_instruments: int
    max_capabilities: int
    max_findings_per_row: int
    max_text_chars: int
    max_url_chars: int


def _no_constants(token: str) -> object:
    raise ValueError("non-finite number " + token)


def _unique_keys(pairs: list[tuple[str, object]]) -> Json:
    result: Json = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key " + key)
        result[key] = value
    return result


def load_json(raw: bytes) -> object:
    return json.loads(raw, parse_constant=_no_constants, object_pairs_hook=_unique_keys)


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("expected array")
    return value


def _obj(value: object) -> Json:
    if not isinstance(value, dict):
        raise TypeError("expected object")
    return cast(Json, value)


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected string")
    return value


def _opt_str(value: object) -> str | None:
    return None if value is None else _str(value)


def _bounds_findings(doc: Json, bounds: DocumentBounds) -> list[Finding]:
    found: list[Finding] = []

    def over(pointer: str, size: int, limit: int) -> None:
        if size > limit:
            found.append(Finding("B", pointer, f"{size} exceeds {limit}"))

    conditions = _list(doc.get("conditions", []))
    over("/conditions", len(conditions), bounds.max_conditions)
    over("/instruments", len(_list(doc.get("instruments", []))), bounds.max_instruments)
    over("/capabilities", len(_list(doc.get("capabilities", []))), bounds.max_capabilities)
    for r, row in enumerate(conditions[: bounds.max_conditions]):
        item = _obj(row)
        tests = _list(item.get("confirming_tests", []))
        over(f"/conditions/{r}/confirming_tests", len(tests), bounds.max_tests_per_row)
        over(
            f"/conditions/{r}/report_only_findings",
            len(_list(item.get("report_only_findings", []))),
            bounds.max_findings_per_row,
        )
        over(f"/conditions/{r}/cliff_url", len(_str(item.get("cliff_url", ""))), bounds.max_url_chars)
        for t, test in enumerate(tests[: bounds.max_tests_per_row]):
            instruments = _list(_obj(test).get("instruments", []))
            over(
                f"/conditions/{r}/confirming_tests/{t}/instruments",
                len(instruments),
                bounds.max_instruments_per_test,
            )
    for pointer, text in _strings(doc, ""):
        over(
            pointer,
            len(text),
            bounds.max_url_chars if pointer.endswith("/cliff_url") else bounds.max_text_chars,
        )
    return found


def _strings(value: object, pointer: str) -> list[tuple[str, str]]:
    """Every string in the document with its pointer; iterative and bounded by the document size cap."""
    out: list[tuple[str, str]] = []
    stack: list[tuple[str, object]] = [(pointer, value)]
    while stack:
        here, item = stack.pop()
        if isinstance(item, str):
            out.append((here, item))
        elif isinstance(item, dict):
            stack.extend((f"{here}/{k}", v) for k, v in item.items())
        elif isinstance(item, list):
            stack.extend((f"{here}/{i}", v) for i, v in enumerate(item))
    return sorted(out)


def _instrument(item: Json) -> Instrument:
    name = _str(item["name"])
    return Instrument(
        id=_str(item["id"]),
        name=name,
        needs_text=_str(item.get("needs_text", name)),  # schema default: name
        capability_codes=tuple(_str(c) for c in _list(item["capability_codes"])),
        method=_str(item.get("method", "manual_instrument")),  # schema default
    )


def _capability(item: Json) -> CapabilityWords:
    duration = item["expected_duration_seconds"]
    if duration is not None and (not isinstance(duration, int) or isinstance(duration, bool)):
        raise TypeError("expected_duration_seconds")
    operator = _str(item["operator_requirement"])
    if operator not in ("self", "crewmate", "crew_medical_officer"):
        raise ValueError("operator_requirement")
    return CapabilityWords(
        code=_str(item["code"]),
        label=_str(item["label"]),
        body_region=_opt_str(item["body_region"]),
        operator_requirement=cast(Operator, operator),
        expected_duration_seconds=duration,
        basis=_str(item["basis"]),
    )


def _canonical(value: object) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True, separators=(",", ":"))


def _row(item: Json) -> ConditionRow:
    review = _str(item["review_state"])
    if review not in ("unreviewed", "reviewed", "rejected"):
        raise ValueError("review_state")
    tests = tuple(
        ConfirmingTest(
            index=t,
            name=_str(_obj(test)["name"]),
            test_id=_opt_str(_obj(test).get("test_id")),
            instruments=tuple(_str(i) for i in _list(_obj(test)["instruments"])),
        )
        for t, test in enumerate(_list(item["confirming_tests"]))
    )
    return ConditionRow(
        condition_id=_str(item["condition_id"]),
        name=_str(item["name"]),
        body_regions=tuple(_str(r) for r in _list(item["body_regions"])),
        tests=tests,
        cliff_url=_str(item["cliff_url"]),
        report_only_findings=tuple(_str(f) for f in _list(item.get("report_only_findings", []))),
        review_state=review,  # type: ignore[arg-type]
        written_by=_opt_str(item["written_by"]),
        checked_by=_opt_str(item["checked_by"]),
        plan_id=_opt_str(item["plan_id"]),
        ground_needs_json=_canonical(item["ground_needs"]),
        treatment_scope_json=_canonical(item["treatment_scope"]),
        rtdc_json=_canonical(item["rtdc"]),
        source_ids=tuple(_str(s) for s in _list(item.get("source_ids", []))),
    )


def _build(
    doc: Json, version: str, environments: tuple[str, ...]
) -> tuple[KitMapDoc, tuple[ConditionRow, ...]]:
    kits = _obj(doc["kits"])
    supplies = tuple(_obj(s) for s in _list(doc["supplies"]))
    all_rows = tuple(_row(_obj(r)) for r in _list(doc["conditions"]))
    parsed = KitMapDoc(
        version=version,
        label=_str(doc["version"]),
        example=bool(doc["example"]) if isinstance(doc["example"], bool) else _fail("example"),
        notice=_opt_str(doc.get("notice")),
        instruments=tuple(_instrument(_obj(i)) for i in _list(doc["instruments"])),
        capabilities=tuple(_capability(_obj(c)) for c in _list(doc["capabilities"])),
        kits={e: frozenset(_str(i) for i in _list(kits[e])) for e in environments},
        candidates=tuple(_str(c) for c in _list(doc["candidates"])),
        candidate_supplies=tuple(_str(c) for c in _list(doc.get("candidate_supplies", []))),
        candidate_skills=tuple(_str(c) for c in _list(doc.get("candidate_skills", []))),
        supplies={
            _str(s["supply_id"]): SupplyDef(
                _str(s["supply_id"]), _str(s["name"]), _str(s["unit"]), s["consumable"] is True
            )
            for s in supplies
        },
        conditions=tuple(r for r in all_rows if r.review_state != "rejected"),
    )
    return parsed, all_rows


def _fail(what: str) -> bool:
    raise TypeError(what)


def _references(doc: KitMapDoc, raw: Json, rows: tuple[ConditionRow, ...]) -> list[Finding]:
    found: list[Finding] = []
    known = {i.id for i in doc.instruments}
    kits = _obj(raw["kits"])
    for env in sorted(doc.kits):
        for i, instrument in enumerate(_list(kits[env])):
            if instrument not in known:
                found.append(Finding("11a", f"/kits/{env}/{i}", "unknown instrument"))
    for i, instrument in enumerate(doc.candidates):
        if instrument not in known:
            found.append(Finding("11a", f"/candidates/{i}", "unknown instrument"))
    for r, row in enumerate(rows):
        for test in row.tests:
            for t, instrument in enumerate(test.instruments):
                if instrument not in known:
                    found.append(
                        Finding(
                            "11a",
                            f"/conditions/{r}/confirming_tests/{test.index}/instruments/{t}",
                            "unknown instrument",
                        )
                    )
    if len(known) != len(doc.instruments):
        found.append(Finding("11a", "/instruments", "duplicate instrument id"))
    provided = {c for i in doc.instruments for c in i.capability_codes}
    codes = [c.code for c in doc.capabilities]
    for code in sorted(provided):
        if codes.count(code) != 1:
            found.append(Finding("11b", "/capabilities", f"capability {code} needs exactly one entry"))
    for c, code in enumerate(codes):
        if code not in provided:
            found.append(Finding("11b", f"/capabilities/{c}/code", "no instrument provides it"))
    return found


def _row_findings(
    doc: KitMapDoc, rows: tuple[ConditionRow, ...], known_conditions: frozenset[str] | None
) -> list[Finding]:
    found: list[Finding] = []
    methods = {i.id: i.method for i in doc.instruments}
    seen: set[str] = set()
    for r, row in enumerate(rows):
        if row.condition_id in seen or (
            known_conditions is not None and row.condition_id not in known_conditions
        ):
            found.append(Finding("11c", f"/conditions/{r}/condition_id", "unknown or repeated condition"))
        seen.add(row.condition_id)
        if row.review_state == "reviewed" and not row.checked_by:
            found.append(Finding("12", f"/conditions/{r}/checked_by", "a reviewed row names its checker"))
        for test in row.tests:
            for t, instrument in enumerate(test.instruments):
                if methods.get(instrument) == CAMERA_ESTIMATE:
                    found.append(
                        Finding(
                            "CAM",
                            f"/conditions/{r}/confirming_tests/{test.index}/instruments/{t}",
                            "a camera estimate never confirms a condition",
                        )
                    )
    return found


def parse_kit_map(
    raw: bytes,
    bounds: DocumentBounds,
    environments: tuple[str, ...],
    known_conditions: frozenset[str] | None,
    digest: Callable[[bytes], str] = lambda b: hashlib.sha256(b).hexdigest(),
) -> KitMapDoc:
    """Raises DocumentInvalid with every finding; never another exception for any input bytes."""
    if len(raw) > bounds.max_document_bytes:
        raise DocumentInvalid((Finding("B", "", f"{len(raw)} bytes exceeds {bounds.max_document_bytes}"),))
    try:
        loaded = _obj(load_json(raw))
        bounded = _bounds_findings(loaded, bounds)
        if bounded:
            raise DocumentInvalid(tuple(bounded))
        doc, rows = _build(loaded, digest(raw), environments)
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise DocumentInvalid(
            (Finding("1", "", "not a valid kit-map document: " + type(error).__name__),)
        ) from None
    findings = _references(doc, loaded, rows) + _row_findings(doc, rows, known_conditions)
    if findings:
        raise DocumentInvalid(tuple(findings))
    return doc


def as_mapping(value: object) -> Mapping[str, object]:
    return _obj(value)
