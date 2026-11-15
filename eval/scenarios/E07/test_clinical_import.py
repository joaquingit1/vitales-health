"""E07-02 scenario: a clinical report file becomes typed drafts awaiting confirmation, never a device reading.

The report is a SIM FHIR R4 Bundle (HTEST security label, SYSTEM 7.3) authored for this scenario; the
mapping is the scenario's configuration document, sourced to the SYSTEM 7.3 LOINC and UCUM table. Nothing is
posted to the hub: the confirmation boundary is E09's private-report flow (P-E07-2).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import e07_harness as h  # noqa: E402
from crew_health_adapter_oximeter.fhir_import.service import ImportService  # noqa: E402
from crew_health_adapter_oximeter.fhir_import.settings import load_settings  # noqa: E402

pytestmark = pytest.mark.trace("T-E07-E2E-06", "E07-02")


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def _service() -> ImportService:
    return ImportService(
        h.asset("clinical-import-mapping.json"), load_settings(h.asset("import-settings.json")), FixedClock()
    )


def test_report_import_keeps_typed_findings_and_holds_what_it_cannot_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    raw = (h.ASSETS / "clinical-report.sim.fhir.json").read_bytes()

    result = _service().import_file(
        raw, expected_patient_reference="Patient/mara", expected_examination_date=date(2026, 11, 13)
    )

    assert result.status == "awaiting_confirmation"
    (spo2,) = result.numeric
    assert (spo2.indicator_code, spo2.value, spo2.ucum) == ("spo2", Decimal(96), "%")
    assert spo2.acquisition_method == "imported_report"
    assert spo2.device_reference is None
    (ecg,) = result.qualitative
    assert ecg.finding_kind == "ecg_impression"
    assert ecg.text == "SIM report text: sinus rhythm"
    assert ecg.confirmed is False
    held = {item.pointer: item.reason for item in result.held}
    assert held == {"/entry/2/resource": "unit_mismatch", "/entry/3/resource": "wrong_subject"}
    assert hub.observations.drafts == []
    assert (
        _service()
        .import_file(
            raw, expected_patient_reference="Patient/mara", expected_examination_date=date(2026, 11, 13)
        )
        .import_id
        == result.import_id
    )


def test_import_assets_validate_against_the_shipped_mapping_schema() -> None:
    schema = files("crew_health_adapter_oximeter").joinpath("schemas", "clinical-import-mapping.schema.json")
    Draft202012Validator(json.loads(schema.read_text(encoding="utf-8"))).validate(
        h.asset("clinical-import-mapping.json")
    )
