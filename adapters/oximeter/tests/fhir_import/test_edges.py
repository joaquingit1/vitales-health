"""Adversarial FHIR shapes, exact values, provenance and isolation of hardware state."""

import json
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_adapter_oximeter.fhir_import.domain import ImportRejected
from crew_health_adapter_oximeter.fhir_import.service import ImportService
from crew_health_adapter_oximeter.fhir_import.settings import ImportSettings
from crew_health_device.application.health import HealthTracker, manifest

from ..helpers import Clock as DeviceClock
from ..helpers import profile
from .test_import import Clock, bundle, mapping_document, observation, run, service


@pytest.mark.trace("T-E07-OX-04", "V21")
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("2026-11-12T10:00:00+02:99", "time_ambiguous"),
        ("2026-11-12T10:00:00+14:01", "time_ambiguous"),
        ("2026-02-30T10:00:00Z", "time_ambiguous"),
        ("0001-01-01T00:00:00+14:00", "time_ambiguous"),
        (1, "time_ambiguous"),
    ],
)
def test_invalid_effective_times(value: object, reason: str) -> None:
    item = observation()
    item["effectiveDateTime"] = value
    assert run(service(), bundle(item)).held[0].reason == reason


@pytest.mark.trace("T-E07-OX-04", "V21", "V38")
def test_effective_period_and_local_date_and_all_final_statuses() -> None:
    items: list[dict[str, object]] = []
    for status in ("final", "amended", "corrected"):
        item = observation()
        del item["effectiveDateTime"]
        item["effectivePeriod"] = {"start": "2026-11-12T23:30:00-02:00"}
        item["status"] = status
        item["device"] = {}
        item["performer"] = [None, {}, {"reference": "Practitioner/source"}]
        items.append(item)
    result = run(service(), bundle(*items), examination=date(2026, 11, 12))
    assert len(result.numeric) == 3 and not result.held
    assert all(n.device_reference is None and n.performer == ("Practitioner/source",) for n in result.numeric)
    assert all(n.examined_at.isoformat() == "2026-11-12T23:30:00-02:00" for n in result.numeric)


@pytest.mark.trace("T-E07-OX-04", "V15", "V21")
@pytest.mark.parametrize(
    "quantity",
    [
        None,
        {},
        {"value": "72", "system": "http://unitsofmeasure.org", "code": "/min"},
        {"value": True, "system": "http://unitsofmeasure.org", "code": "/min"},
    ],
)
def test_numeric_requires_measured_decimal(quantity: object) -> None:
    item = observation()
    item["valueQuantity"] = quantity
    result = run(service(), bundle(item))
    assert result.held[0].reason == "value_missing" and not result.numeric


@pytest.mark.trace("T-E07-OX-04", "V15", "V21")
@pytest.mark.parametrize(
    ("system", "unit"), [("urn:other", "/min"), (None, "/min"), ("http://unitsofmeasure.org", None)]
)
def test_unit_system_and_code_must_match(system: object, unit: object) -> None:
    item = observation()
    item["valueQuantity"] = {"value": 72, "system": system, "code": unit}
    assert run(service(), bundle(item)).held[0].reason == "unit_mismatch"


@pytest.mark.trace("T-E07-OX-04", "V15")
def test_temperature_fahrenheit_is_never_converted() -> None:
    document = mapping_document()
    cast(list[dict[str, object]], document["numeric"]).append(
        {
            "system": "http://loinc.org",
            "code": "8310-5",
            "indicator_code": "temperature",
            "accepted_ucum": ["Cel"],
        }
    )
    result = run(service(document=document), bundle(observation("8310-5", "[degF]")))
    assert not result.numeric and result.held[0].reason == "unit_mismatch"


@pytest.mark.trace("T-E07-OX-04", "E07-02", "V18", "V38")
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"valueQuantity": {"value": 1}}, "unexpected_value_type"),
        ({"valueString": 2}, "unexpected_value_type"),
        ({}, "value_missing"),
        ({"valueString": "é" * 513}, "text_too_long"),
        (
            {"valueCodeableConcept": {"coding": [{"system": "s", "code": "c", "display": "a" * 1025}]}},
            "text_too_long",
        ),
    ],
)
def test_qualitative_holds(changes: dict[str, object], reason: str) -> None:
    item = observation("8601-7")
    del item["valueQuantity"]
    item.update(changes)
    result = run(service(), bundle(item))
    assert result.held[0].reason == reason
    assert not result.numeric and not result.qualitative


@pytest.mark.trace("T-E07-OX-04", "E07-02", "V18", "V38")
def test_qualitative_text_and_codings_preserved_without_interpretation() -> None:
    text = observation("8601-7")
    del text["valueQuantity"]
    text["valueString"] = "é" * 512
    concept = dict(text)
    del concept["valueString"]
    concept["valueCodeableConcept"] = {
        "coding": [
            {"system": "source", "code": "x"},
            {"system": "source", "code": "y", "display": "Quoted source"},
            {"system": 3, "code": 4},
            None,
        ]
    }
    result = run(service(), bundle(text, concept))
    assert not result.held and not result.numeric
    assert result.qualitative[0].text == "é" * 512
    assert result.qualitative[0].source_codings == ()
    assert result.qualitative[1].text is None
    assert result.qualitative[1].source_codings == (("source", "x", None), ("source", "y", "Quoted source"))


@pytest.mark.trace("T-E07-OX-04", "AGENTS 2 input checks")
@pytest.mark.parametrize(
    "raw",
    [
        b"null",
        b"[]",
        b"true",
        b"42",
        b'"string"',
        b'{"resourceType":null}',
        b'{"resourceType":[]}',
    ],
)
def test_top_level_requires_supported_resource_object(raw: bytes) -> None:
    with pytest.raises(ImportRejected, match="unsupported_resource"):
        run(service(), raw)


@pytest.mark.trace("T-E07-OX-04", "AGENTS 2 input checks")
def test_bad_entry_shapes_are_bounded_and_held() -> None:
    invalid_entries: tuple[object, ...] = (None, {}, "entry")
    for entries in invalid_entries:
        with pytest.raises(ImportRejected, match="malformed"):
            run(service(), json.dumps({"resourceType": "Bundle", "entry": entries}).encode())
    assert run(service(), b'{"resourceType":"Bundle"}').held == ()
    raw = json.dumps({"resourceType": "Bundle", "entry": [None, {"resource": 3}, {}]}).encode()
    result = run(service(), raw)
    assert len(result.held) == 3 and all(h.reason == "unsupported_resource" for h in result.held)


@pytest.mark.trace("T-E07-OX-04", "V16", "AGENTS 2 input checks")
def test_root_observation_pointer_hash_exact_decimal_and_bounds() -> None:
    raw = (
        json.dumps(observation()).replace('"value": 72', '"value": 72.12345678901234567890123456789').encode()
    )
    result = run(service(settings=ImportSettings(len(raw), 1, 4, 1)), raw)
    finding = result.numeric[0]
    assert finding.pointer == ""  # RFC 6901: empty pointer addresses the entire document.
    assert str(finding.value) == "72.12345678901234567890123456789"
    assert finding.source_sha256 == result.source_sha256 == sha256(raw).hexdigest()
    expected_id = "imp_" + sha256((sha256(raw).hexdigest() + "Patient/crew-1").encode()).hexdigest()[:32]
    assert result.import_id == expected_id
    with pytest.raises(ImportRejected, match="too_deep"):
        run(service(settings=ImportSettings(len(raw), 1, 3, 1)), raw)


@pytest.mark.trace("T-E07-OX-04", "AGENTS 2 input checks")
def test_depth_ignores_quoted_delimiters_and_escapes() -> None:
    item = observation()
    item["ignored"] = '[" { \\ ] } "'
    assert (
        len(run(service(settings=ImportSettings(10000, 10, 4, 100)), json.dumps(item).encode()).numeric) == 1
    )
    for raw in (b'{"a":"\\ud800"}', b'{"\\udfff":1}', b'{"value":1e9999999999999999999999}'):
        with pytest.raises(ImportRejected) as error:
            run(service(), raw)
        assert error.value.reason in ("malformed", "nonfinite")


@pytest.mark.trace("T-E07-OX-04", "V16", "V21")
def test_retry_capacity_and_validation_context() -> None:
    importer = service(settings=ImportSettings(10000, 1, 32, 100))
    raw = bundle(observation())
    first = run(importer, raw)
    assert run(importer, raw) is first
    other = run(importer, raw, patient="Patient/other")
    assert other.import_id != first.import_id and other.held[0].reason == "wrong_subject"
    importer = service()
    assert run(importer, raw).numeric
    assert run(importer, raw, examination=date(2020, 1, 1)).held[0].reason == "date_mismatch"
    with pytest.raises(ImportRejected, match="subject_missing"):
        run(importer, raw, patient=" ")
    clock = Clock()
    clock.instant = datetime(2026, 11, 14)
    with pytest.raises(ImportRejected, match="invalid_clock"):
        run(ImportService(mapping_document(), ImportSettings(10000, 10, 32, 100), clock), raw)


@pytest.mark.trace("T-E07-OX-04", "V18", "V38")
def test_conflicting_code_targets_are_held_and_agreeing_translations_are_one_finding() -> None:
    item = observation("2708-6", "%")
    code = cast(dict[str, object], item["code"])
    rows = cast(list[dict[str, str]], code["coding"])
    rows.append({"system": "http://loinc.org", "code": "59408-5"})
    assert len(run(service(), bundle(item)).numeric) == 1
    rows.append({"system": "http://loinc.org", "code": "8601-7"})
    result = run(service(), bundle(item))
    assert not result.numeric and not result.qualitative and result.held[0].reason == "ambiguous_code"


@pytest.mark.trace("T-E07-OX-04", "R26")
def test_import_preserves_manifest_and_hardware_availability() -> None:
    clock = DeviceClock()
    health = HealthTracker(clock, 1000)

    def current() -> dict[str, object]:
        return manifest(
            profile(),
            family="oximeter",
            service="0x1822",
            characteristics=("0x2A5E", "0x2A5F"),
            source_origin="LIVE",
            connected_state=health.state(),
        )

    initial_health = health.state()
    before = current()
    run(service(), bundle(observation()))
    assert current() == before
    assert before["hardware_compatibility"] == "unmeasured"
    operations = cast(list[dict[str, object]], before["operations"])
    assert operations[0] == {
        "mode": "connected",
        "origin": "LIVE",
        "status": "unavailable",
        "reason": "source_not_connected",
    }
    assert not any(op["mode"] == "import" for op in operations)
    assert health.state() == initial_health


@pytest.mark.trace("T-E07-OX-04", "AGENTS 2 input checks")
@given(
    st.sampled_from(
        ("subject", "effectiveDateTime", "status", "code", "valueQuantity", "performer", "device")
    ),
    st.recursive(
        st.none() | st.booleans() | st.integers() | st.text(),
        lambda child: st.lists(child, max_size=5) | st.dictionaries(st.text(), child, max_size=5),
        max_leaves=30,
    ),
)
def test_arbitrary_json_shapes_stay_within_typed_boundary(key: str, value: object) -> None:
    # Vary one field at a time so subject validation cannot mask nested value/attribution parsing.
    item = observation()
    item[key] = value
    try:
        result = run(service(), bundle(item))
    except ImportRejected:
        return
    assert result.status == "awaiting_confirmation"
    assert len(result.numeric) + len(result.held) == 1 and not result.qualitative
    assert all(n.value.is_finite() and n.ucum == "/min" for n in result.numeric)


@pytest.mark.trace("T-E07-OX-04", "AGENTS 2 input checks", "AGENTS 1.8")
@given(
    st.decimals(
        min_value="-1000000000", max_value="1000000000", allow_nan=False, allow_infinity=False, places=20
    )
)
def test_finite_json_numbers_preserve_exact_decimal_without_conversion(value: Decimal) -> None:
    raw = json.dumps(observation()).replace('"value": 72', '"value": ' + str(value)).encode()
    result = run(service(), raw)
    assert result.numeric[0].value == value
    assert result.numeric[0].ucum == "/min" and not result.held


@pytest.mark.trace("T-E07-OX-04", "V16", "V38", "AGENTS 1.7")
def test_every_output_tuple_is_sorted_and_no_private_text_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    qualitative = observation("8601-7")
    del qualitative["valueQuantity"]
    qualitative["valueString"] = "private source narrative"
    items = [observation(), qualitative, observation("unknown")] * 5
    result = run(service(), bundle(*items))
    for pointers in (
        [n.pointer for n in result.numeric],
        [q.pointer for q in result.qualitative],
        [h.pointer for h in result.held],
    ):
        assert pointers == sorted(pointers, key=lambda pointer: int(pointer.split("/")[2]))
    assert len(result.numeric) == len(result.qualitative) == len(result.held) == 5
    assert caplog.records == []
    assert result.qualitative[0].source_sha256 == result.numeric[0].source_sha256 == result.source_sha256


@pytest.mark.trace("T-E07-OX-04", "E07-02", "V16")
def test_held_items_follow_numeric_json_pointer_order() -> None:
    result = run(service(), bundle(*(observation("unknown") for _ in range(11))))
    assert [h.pointer for h in result.held] == [f"/entry/{index}/resource" for index in range(11)]
    assert all(h.reason == "unmapped_code" for h in result.held)
    assert result.numeric == ()
    assert result.qualitative == ()
