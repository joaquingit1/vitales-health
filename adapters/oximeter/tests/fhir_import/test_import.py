"""Clinical import contract, using independent SYSTEM 7.3 LOINC/UCUM facts."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_adapter_oximeter.fhir_import.domain import ImportRejected, ImportResult
from crew_health_adapter_oximeter.fhir_import.service import ImportService
from crew_health_adapter_oximeter.fhir_import.settings import ImportSettings


class Clock:
    def __init__(self) -> None:
        self.instant = datetime(2026, 11, 14, tzinfo=UTC)

    def now(self) -> datetime:
        return self.instant


def mapping_document() -> dict[str, object]:
    # SYSTEM 7.3, lines 1761 to 1763. Expectations below are independent golden values.
    return {
        "version": "test-1",
        "provenance": [
            {"path": "docs/SYSTEM.md#7.3", "note": "FHIR R4 4.0.1, LOINC and UCUM"},
            {
                "path": "https://loinc.org/8601-7/",
                "note": "LOINC 8601-7 EKG impression, https://loinc.org/8601-7/",
            },
        ],
        "numeric": [
            {"system": "http://loinc.org", "code": code, "indicator_code": indicator, "accepted_ucum": [unit]}
            for code, indicator, unit in (
                ("8867-4", "heart_rate", "/min"),
                ("2708-6", "spo2", "%"),
                ("59408-5", "spo2", "%"),
                ("8480-6", "systolic_bp", "mm[Hg]"),
                ("8462-4", "diastolic_bp", "mm[Hg]"),
            )
        ],
        "interpretations": [
            {"system": "http://loinc.org", "code": "8601-7", "finding_kind": "ecg_impression"}
        ],
    }


def observation(code: str = "8867-4", unit: str = "/min") -> dict[str, object]:
    return {
        "resourceType": "Observation",
        "status": "final",
        "subject": {"reference": "Patient/crew-1"},
        "effectiveDateTime": "2026-11-12T10:15:30+02:00",
        "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
        "valueQuantity": {"value": 72, "system": "http://unitsofmeasure.org", "code": unit},
        "performer": [{"reference": "Practitioner/clinician-1"}],
        "device": {"reference": "Device/source-text-only"},
    }


def bundle(*resources: object) -> bytes:
    return json.dumps(
        {"resourceType": "Bundle", "entry": [{"resource": item} for item in resources]}
    ).encode()


def service(
    *, settings: ImportSettings | None = None, document: dict[str, object] | None = None
) -> ImportService:
    return ImportService(
        document or mapping_document(), settings or ImportSettings(100000, 100, 32, 1024), Clock()
    )


def run(
    importer: ImportService, raw: bytes, *, patient: str = "Patient/crew-1", examination: date | None = None
) -> ImportResult:
    return importer.import_file(
        raw, expected_patient_reference=patient, expected_examination_date=examination
    )


@pytest.mark.trace("T-E07-OX-04", "R08", "E07-02", "V18", "V38")
def test_numeric_and_qualitative_are_separate_attributed_drafts() -> None:
    ecg = observation("8601-7")
    del ecg["valueQuantity"]
    ecg["valueCodeableConcept"] = {
        "coding": [{"system": "urn:source:ecg", "code": "report-1", "display": "Source interpretation"}],
        "text": "Source clinician impression",
    }
    result = run(service(), bundle(observation(), observation("2708-6", "%"), ecg))
    assert result.status == "awaiting_confirmation"
    assert [(n.indicator_code, n.value, n.ucum) for n in result.numeric] == [
        ("heart_rate", Decimal(72), "/min"),
        ("spo2", Decimal(72), "%"),
    ]
    assert len(result.qualitative) == 1
    finding = result.qualitative[0]
    assert finding.finding_kind == "ecg_impression"
    assert finding.interpreter == ("Practitioner/clinician-1",)
    assert finding.source_codings == (("urn:source:ecg", "report-1", "Source interpretation"),)
    assert finding.text == "Source clinician impression"
    assert finding.confirmed is False
    assert not hasattr(finding, "value") and not hasattr(finding, "context")
    assert all(not hasattr(n, "device_id") and not hasattr(n, "connected") for n in result.numeric)
    assert result.numeric[0].device_reference == "Device/source-text-only"
    assert result.numeric[0].performer == ("Practitioner/clinician-1",)
    assert result.numeric[0].acquisition_method == "imported_report"
    assert result.numeric[0].examined_at.isoformat() == "2026-11-12T10:15:30+02:00"
    assert result.numeric[0].imported_at == datetime(2026, 11, 14, tzinfo=UTC)
    assert finding.imported_at == result.numeric[0].imported_at
    assert result.held == ()


@pytest.mark.trace("T-E07-OX-04", "V21")
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"subject": {"reference": "Patient/other"}}, "wrong_subject"),
        ({"subject": {}}, "subject_missing"),
        ({"effectiveDateTime": None}, "time_missing"),
        ({"effectiveDateTime": "2026-11-11T23:59:59Z"}, "date_mismatch"),
        (
            {"valueQuantity": {"value": 98, "system": "http://unitsofmeasure.org", "code": "1"}},
            "unit_mismatch",
        ),
        ({"status": "preliminary"}, "not_final"),
        ({"effectiveDateTime": "2026-11-15T00:00:00Z"}, "future_time"),
        ({"effectiveDateTime": "2026-11-12"}, "time_ambiguous"),
        ({"effectiveDateTime": "2026-11-12T10:00:00"}, "time_ambiguous"),
    ],
)
def test_observation_holds(change: dict[str, object], reason: str) -> None:
    item = observation("2708-6", "%")
    item.update(change)
    result = run(service(), bundle(item), examination=date(2026, 11, 12))
    assert result.numeric == ()
    assert result.qualitative == ()
    assert [(h.pointer, h.reason) for h in result.held] == [("/entry/0/resource", reason)]


@pytest.mark.trace("T-E07-OX-04", "E07-02", "AGENTS 1.5")
@pytest.mark.parametrize("comparator", ["<", "<=", ">=", ">", "ad", None, "unknown"])
def test_quantity_comparator_is_held_without_changing_exact_values(comparator: object) -> None:
    item = observation("2708-6", "%")
    quantity = cast(dict[str, object], item["valueQuantity"])
    quantity["value"] = 85
    exact = json.loads(json.dumps(item))
    quantity["comparator"] = comparator
    result = run(service(), bundle(item, exact))
    assert [(h.pointer, h.reason) for h in result.held] == [("/entry/0/resource", "comparator_unsupported")]
    assert [(n.pointer, n.value, n.ucum) for n in result.numeric] == [("/entry/1/resource", Decimal(85), "%")]


@pytest.mark.trace("T-E07-OX-04", "E07-02", "V21")
@pytest.mark.parametrize(
    ("timestamp", "accepted"),
    [
        ("2026-11-12T22:30:00-03:00", True),
        ("2026-11-12T00:30:00+03:00", True),
        ("2026-11-13T00:30:00+00:00", False),
        ("2026-11-13T00:30:00+03:00", False),
    ],
)
def test_examination_date_uses_the_report_local_offset(timestamp: str, accepted: bool) -> None:
    item = observation()
    item["effectiveDateTime"] = timestamp
    result = run(service(), bundle(item), examination=date(2026, 11, 12))
    if accepted:
        assert result.held == ()
        assert result.numeric[0].examined_at.isoformat() == timestamp
    else:
        assert result.numeric == ()
        assert result.held[0].reason == "date_mismatch"


@pytest.mark.trace("T-E07-OX-04", "E07-02", "V16", "AGENTS 3.2")
@given(st.integers(min_value=1, max_value=5))
def test_result_cache_evicts_oldest_and_recomputes_deterministically(capacity: int) -> None:
    importer = service(settings=ImportSettings(100000, capacity, 32, 1024))
    inputs: list[bytes] = []
    for index in range(capacity + 1):
        item = observation()
        item["id"] = f"source-{index}"
        inputs.append(bundle(item))
    first = run(importer, inputs[0])
    for raw in inputs[1:capacity]:
        run(importer, raw)
    # Reading the oldest cached result does not change insertion order.
    assert run(importer, inputs[0]) is first
    newest = run(importer, inputs[-1])
    assert newest.numeric and newest.held == ()
    assert run(importer, inputs[-1]) is newest
    replay = run(importer, inputs[0])
    assert replay is not first
    assert replay == first


@pytest.mark.trace("T-E07-OX-04", "E07-02", "V16", "AGENTS 3.2")
def test_evicted_result_uses_current_clock_when_revalidated() -> None:
    clock = Clock()
    importer = ImportService(mapping_document(), ImportSettings(100000, 1, 32, 1024), clock)
    raw = bundle(observation())
    first = run(importer, raw)
    run(importer, raw, examination=date(2026, 11, 12))
    clock.instant += timedelta(days=1)
    replay = run(importer, raw)
    assert replay.import_id == first.import_id
    assert replay.source_sha256 == first.source_sha256
    assert replay.numeric[0].value == first.numeric[0].value
    assert replay.numeric[0].examined_at == first.numeric[0].examined_at
    assert replay.numeric[0].imported_at == clock.instant
    assert run(importer, raw) is replay


@pytest.mark.trace("T-E07-OX-04", "V16")
def test_retry_is_identical_even_when_clock_advances() -> None:
    clock = Clock()
    importer = ImportService(mapping_document(), ImportSettings(100000, 100, 32, 1024), clock)
    raw = bundle(observation())
    first = run(importer, raw)
    clock.instant += timedelta(days=1)
    second = run(importer, raw)
    assert first == second
    assert first.numeric[0].imported_at == second.numeric[0].imported_at
    assert run(importer, raw, patient="Patient/other").import_id != first.import_id


@pytest.mark.trace("T-E07-OX-04", "AGENTS 2 input checks")
@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"x" * 100001, "too_large"),
        (b"\xff", "malformed"),
        (b"{", "malformed"),
        (b'{"resourceType":"Observation","resourceType":"Bundle"}', "malformed"),
        (b'{"value":NaN}', "nonfinite"),
        (b'{"value":Infinity}', "nonfinite"),
        (b'{"value":-Infinity}', "nonfinite"),
        (b'{"resourceType":"Patient"}', "unsupported_resource"),
    ],
)
def test_file_rejections(raw: bytes, reason: str) -> None:
    with pytest.raises(ImportRejected) as error:
        run(service(), raw)
    assert error.value.reason == reason


@pytest.mark.trace("T-E07-OX-04", "AGENTS 2 input checks")
def test_bounds_and_unmapped_code() -> None:
    raw = b"0"
    for _ in range(1000):
        raw = b"[" + raw + b"]"
    with pytest.raises(ImportRejected, match="too_deep"):
        run(service(), raw)
    with pytest.raises(ImportRejected, match="too_many_entries"):
        run(service(settings=ImportSettings(100000, 1, 32, 1024)), bundle(observation(), observation()))
    result = run(service(), bundle(observation("unknown"), {"resourceType": "Patient"}))
    assert [(h.pointer, h.reason) for h in result.held] == [
        ("/entry/0/resource", "unmapped_code"),
        ("/entry/1/resource", "unsupported_resource"),
    ]
    assert result.held[0].codings == (("http://loinc.org", "unknown", None),)


@pytest.mark.trace("T-E07-OX-04", "AGENTS 2 input checks")
@given(st.binary(max_size=2048))
def test_arbitrary_bytes_never_escape_typed_boundary(raw: bytes) -> None:
    try:
        result = run(service(), raw)
    except ImportRejected:
        return
    assert result.status == "awaiting_confirmation"


@pytest.mark.trace("V75", "AGENTS 1.1")
def test_mapping_edit_enables_temperature_without_code_change() -> None:
    document = mapping_document()
    raw = bundle(observation("8310-5", "Cel"))
    assert run(service(document=document), raw).held[0].reason == "unmapped_code"
    cast(list[dict[str, object]], document["numeric"]).append(
        {
            "system": "http://loinc.org",
            "code": "8310-5",
            "indicator_code": "temperature",
            "accepted_ucum": ["Cel"],
        }
    )
    result = run(service(document=document), raw)
    assert [(n.indicator_code, n.ucum) for n in result.numeric] == [("temperature", "Cel")]
    assert result.held == ()
