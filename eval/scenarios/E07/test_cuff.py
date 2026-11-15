"""E07 scenario: a kit cuff's blood pressure panel reaches E05 and is recorded whole.

The cuff decodes one SIM Blood Pressure Measurement frame (120/80 mmHg, MAP 93) and, through its profile's
blood_pressure panel (SYSTEM 7.3: LOINC 85354-9 with 8480-6 and 8462-4), sends systolic_bp and diastolic_bp in
one attempt. E05 binds the panel through the kit map capability's `components` (systolic_bp, diastolic_bp): it
records both values or neither, and refuses half a panel or a code outside it.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import e07_harness as h  # noqa: E402
import measurement_harness as mh  # noqa: E402
from crew_health.measurement.application.ports import AdapterAssignment  # noqa: E402

pytestmark = pytest.mark.trace("T-E07-BP-01", "T-E07-BP-03", "T-E07-E2E-03")

CUFF_ID = "adp_cuff"
DEVICE = "dev_kit_cuff"


def _cuff_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, h.AdapterProcess, str]:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    hub.sessions.other("cuff", "adapter", "adapter", CUFF_ID)
    hub.assignments.by_method["device"] = AdapterAssignment(CUFF_ID, "device", DEVICE)
    process = h.adapter_process(
        tmp_path, h.hub_sender(hub, "cuff"), "cuff-panel.recording.json", family="cuff", adapter_id=CUFF_ID
    )
    hub.readiness.ready = h.readiness_from(process)
    request_id = h.started_request(hub, h.request_body("blood_pressure"), "blood-pressure")
    return hub, process, request_id


def test_blood_pressure_panel_is_recorded_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, process, request_id = _cuff_hub(tmp_path, monkeypatch)

    assert (
        h.start_acquisition(process, h.binding_for(request_id, "blood_pressure", device_id=DEVICE))
        == "started"
    )

    stored = h.stored_request(hub, request_id)
    assert stored["state"] == "completed"
    (attempt,) = stored["attempts"]
    assert attempt["outcome"] == "success"
    assert [
        (d.code, d.quantity.value, d.quantity.unit.code, d.origin_mode) for d in hub.observations.drafts
    ] == [
        ("systolic_bp", Decimal(120), "mm[Hg]", "SIM"),
        ("diastolic_bp", Decimal(80), "mm[Hg]", "SIM"),
    ]
    assert process.journal.records() == ()


def _panel_body(*codes: str) -> dict[str, object]:
    values = {"systolic_bp": 120, "diastolic_bp": 80, "pulse": 64}
    return {
        "sequence": 1,
        "started_at": "2026-11-13T09:33:00Z",
        "ended_at": "2026-11-13T09:33:30Z",
        "outcome": "success",
        "observations": [
            {
                "patient_id": "mara",
                "episode_id": mh.pulse_body()["episode_id"],
                "code": code,
                "value": values[code],
                "unit": "/min" if code == "pulse" else "mm[Hg]",
                "observed_at": "2026-11-13T09:33:20Z",
                "origin_mode": "SIM",
                "acquisition_method": "device",
                "device_id": DEVICE,
                "context": {},
            }
            for code in codes
        ],
    }


@pytest.mark.parametrize(
    ("codes", "detail"),
    [
        (("systolic_bp",), "panel_incomplete"),
        (("systolic_bp", "diastolic_bp", "pulse"), "code_not_requested"),
    ],
)
def test_half_a_panel_or_a_code_outside_it_is_refused_and_nothing_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codes: tuple[str, ...], detail: str
) -> None:
    hub, _process, request_id = _cuff_hub(tmp_path, monkeypatch)
    response = mh.attempt(hub, "cuff", request_id, _panel_body(*codes), "idem-key-panel-" + detail)

    assert response.status_code == 422, response.json()
    problem = response.json()
    assert problem["code"] == "IDENTITY_MISMATCH"
    assert detail in json.dumps(problem)
    assert h.stored_request(hub, request_id)["attempts"] == []
    assert hub.observations.drafts == []
