"""Configuration boundaries and activation are checked independently of clinical content."""

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from crew_health_adapter_oximeter.fhir_import.domain import ImportMappingError
from crew_health_adapter_oximeter.fhir_import.mapping import load_mapping_file, parse_mapping
from crew_health_adapter_oximeter.fhir_import.service import ImportService
from crew_health_adapter_oximeter.fhir_import.settings import (
    ImportSettings,
    descriptors,
    load_settings,
)
from crew_health_device.application.settings import SettingsError

from .test_import import Clock, mapping_document


def settings_document() -> dict[str, object]:
    return {
        d.key: {"value": d.maximum, "unit": d.unit, "basis": d.basis, "to_be_set_by": "station operator"}
        for d in descriptors()
    }


@pytest.mark.trace("E07-02", "AGENTS 1.1", "AGENTS 3.7")
def test_settings_require_exact_document_shape_and_bounds() -> None:
    assert load_settings(settings_document()) == ImportSettings(10485760, 10000, 64, 65536)
    document = settings_document()
    for descriptor in descriptors():
        assert descriptor.minimum == 1 and descriptor.basis
        cast(dict[str, object], document[descriptor.key])["value"] = 1
    assert load_settings(document) == ImportSettings(1, 1, 1, 1)


@pytest.mark.trace("E07-02", "AGENTS 3.7")
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (None, "missing_or_invalid_entry"),
        ({"value": 1}, "invalid_entry_fields"),
        ({"value": True, "unit": "By", "basis": "bound", "to_be_set_by": "operator"}, "integer_required"),
        ({"value": 0, "unit": "By", "basis": "bound", "to_be_set_by": "operator"}, "out_of_range"),
        ({"value": 10485761, "unit": "By", "basis": "bound", "to_be_set_by": "operator"}, "out_of_range"),
        ({"value": 1, "unit": "s", "basis": "bound", "to_be_set_by": "operator"}, "invalid_unit"),
        ({"value": 1, "unit": "By", "basis": " ", "to_be_set_by": "operator"}, "empty_basis"),
        ({"value": 1, "unit": "By", "basis": "bound", "to_be_set_by": None}, "empty_to_be_set_by"),
    ],
)
def test_invalid_settings(change: object, reason: str) -> None:
    document = settings_document()
    document["import.max_bytes"] = change
    with pytest.raises(SettingsError) as error:
        load_settings(document)
    assert (error.value.key, error.value.reason) == ("import.max_bytes", reason)


@pytest.mark.trace("E07-02", "AGENTS 3.7")
def test_missing_unknown_and_direct_settings_are_checked() -> None:
    with pytest.raises(SettingsError, match="unknown_setting"):
        load_settings({"unexpected": {}})
    with pytest.raises(SettingsError, match="missing_or_invalid_entry"):
        load_settings({})
    with pytest.raises(SettingsError, match="integer_required"):
        ImportSettings(True, 1, 1, 1)
    with pytest.raises(SettingsError, match="out_of_range"):
        ImportSettings(1, 10001, 1, 1)


@pytest.mark.trace("E07-02", "AGENTS 1.1")
def test_mapping_schema_and_duplicate_codes_checked_at_service_construction() -> None:
    valid = parse_mapping(mapping_document())
    assert parse_mapping(valid) == valid
    assert valid.provenance[1][1] == "LOINC 8601-7 EKG impression, https://loinc.org/8601-7/"
    with pytest.raises(ImportMappingError, match="schema_invalid"):
        ImportService(replace(valid, version=""), ImportSettings(10000, 10, 16, 100), Clock())
    for group in ("numeric", "interpretations"):
        document = mapping_document()
        row: dict[str, object] = {"system": "http://loinc.org", "code": "8867-4"}
        row.update(
            {"indicator_code": "pulse", "accepted_ucum": ["/min"]}
            if group == "numeric"
            else {"finding_kind": "impression"}
        )
        cast(list[dict[str, object]], document[group]).append(row)
        with pytest.raises(ImportMappingError) as error:
            parse_mapping(document)
        assert error.value.reason == "duplicate_code"
        assert error.value.pointer.startswith(f"/{group}/")


@pytest.mark.trace("E07-02", "AGENTS 1.1")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provenance", []),
        ("version", 4),
        ("numeric", [{}]),
        ("interpretations", [{"system": "http://loinc.org", "code": "x", "finding_kind": "bad-code"}]),
        (
            "numeric",
            [{"system": "http://loinc.org", "code": "x", "indicator_code": "x", "accepted_ucum": []}],
        ),
        (
            "numeric",
            [{"system": "http://loinc.org", "code": "x", "indicator_code": "x\n", "accepted_ucum": ["Cel"]}],
        ),
    ],
)
def test_invalid_mapping_schema(field: str, value: object) -> None:
    document = mapping_document()
    document[field] = value
    with pytest.raises(ImportMappingError, match="schema_invalid"):
        parse_mapping(document)


@pytest.mark.trace("E07-02", "AGENTS 1.1")
def test_mapping_file_bounded_loading(tmp_path: Path) -> None:
    path = tmp_path / "mapping.json"
    raw = json.dumps(mapping_document()).encode()
    path.write_bytes(raw)
    assert load_mapping_file(path, len(raw)) == parse_mapping(mapping_document())
    with pytest.raises(ImportMappingError, match="too_large"):
        load_mapping_file(path, len(raw) - 1)
    for maximum in (0, 10485761, True):
        with pytest.raises(ImportMappingError, match="invalid_max_bytes"):
            load_mapping_file(path, maximum)
    for content, reason in (
        (b"\xff", "malformed"),
        (b"{", "malformed"),
        (b'{"a":1,"a":2}', "malformed"),
        (b'{"a":NaN}', "malformed"),
        (b"[]", "object_required"),
        (b"{}", "schema_invalid"),
    ):
        path.write_bytes(content)
        with pytest.raises(ImportMappingError, match=reason):
            load_mapping_file(path, 10000)
    path.unlink()
    with pytest.raises(ImportMappingError, match="malformed"):
        load_mapping_file(path, 10000)
