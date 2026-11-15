"""E07 hardware scenarios, run against the simulated twins by default (AGENTS.md section 4).

Each twin (simulators/clinical) emits the GATT notification bytes a real oximeter, thermometer or cuff
emits. They cross the real BLE transport (BleakFrameSource with the twin's BleakClient-shaped client), the
family decoder, the quality checks, the file journal and the HTTP sender, and land in E05's real measurement
router as a recorded observation or a named failure. The twin's clock advances one second per notification,
as a running twin's does, while delivery is paced faster (e07_harness). The twin's device identity says it is
simulated, so the source is SIM through the normal source rule; the adapter has no branch for it. Expected
values are read back from the twin's own notification bytes with an independent IEEE 11073 parse (special
words included), never from the adapter's decoder or profile.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import e07_harness as h  # noqa: E402
import measurement_harness as mh  # noqa: E402
from crew_health.measurement.application.ports import AdapterAssignment  # noqa: E402
from crew_health_device.adapters.twin_source import Family  # noqa: E402
from crew_health_sim_clinical.twin import Disconnect, Notification, Twin  # noqa: E402

pytestmark = [pytest.mark.hardware, pytest.mark.trace("AGENTS 4", "E07-03", "T-E07-E2E-01", "R08", "R26")]

SEED = 7
WINDOW = h.adapter_settings("adapter-settings.twin.json").max_frames

# IEEE 11073-20601 special values: these words are not numbers (NaN, NRes, +INF, -INF, reserved).
SFLOAT_SPECIAL = frozenset((0x07FF, 0x0800, 0x07FE, 0x0802, 0x0801))
FLOAT_SPECIAL = frozenset((0x007FFFFF, 0x00800000, 0x007FFFFE, 0x00800002, 0x00800001))


def _sfloat(raw: bytes) -> Decimal | None:
    value = int.from_bytes(raw, "little")
    if value in SFLOAT_SPECIAL:
        return None
    mantissa, exponent = value & 0x0FFF, value >> 12
    mantissa -= 0x1000 if mantissa >= 0x0800 else 0
    exponent -= 0x10 if exponent >= 0x8 else 0
    return Decimal(mantissa).scaleb(exponent)


def _float(raw: bytes) -> Decimal | None:
    value = int.from_bytes(raw, "little")
    if value in FLOAT_SPECIAL:
        return None
    mantissa, exponent = value & 0xFFFFFF, value >> 24
    mantissa -= 0x1000000 if mantissa >= 0x800000 else 0
    exponent -= 0x100 if exponent >= 0x80 else 0
    return Decimal(mantissa).scaleb(exponent)


@pytest.mark.parametrize(
    ("raw", "parse"),
    [(b"\xff\x07", _sfloat), (b"\x00\x08", _sfloat), (b"\xfe\x07", _sfloat), (b"\xff\xff\x7f\x00", _float)],
)
def test_the_independent_parse_never_reads_a_special_word_as_a_number(raw: bytes, parse: Any) -> None:
    assert parse(raw) is None


def _notifications(family: Family, scenario: str) -> list[Notification]:
    """The measurement notifications the adapter reads in one window (battery reports are not subscribed)."""
    twin = Twin(family, scenario, SEED, start=h.TWIN_START, interval_s=h.TWIN_INTERVAL_S)
    events = list(twin.events(WINDOW))
    return [e for e in events if isinstance(e, Notification) and e.characteristic != "battery_level"]


def _layout_lengths(family: Family) -> dict[str, int]:
    return {n.characteristic: len(n.payload) for n in _notifications(family, "normal")}


def _steady_spo2(scenario: str) -> Decimal:
    """C08: the reading counts once it "stops changing and displays one steady number": the last SpO2 equal to
    the one before it, across every well formed PLX notification in arrival order."""
    lengths = _layout_lengths("oximeter")
    values = [
        _sfloat(n.payload[1:3])
        for n in _notifications("oximeter", scenario)
        if len(n.payload) == lengths[n.characteristic]
    ]
    steady = [v for previous, v in pairwise(values) if v is not None and v == previous]
    assert steady, "the twin never held one value twice"
    return steady[-1]


def _run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, *, family: Family, capability: str
) -> tuple[mh.Hub, h.AdapterProcess, dict[str, Any]]:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    adapter_id, token = h.ADAPTER_ID, h.ADAPTER_TOKEN
    if family != "oximeter":
        adapter_id, token = f"adp_{family}", family
        hub.sessions.other(token, "adapter", "adapter", adapter_id)
        hub.assignments.by_method["device"] = AdapterAssignment(adapter_id, "device", f"dev_kit_{family}")
    process = h.twin_process(
        tmp_path, h.hub_sender(hub, token), f"{scenario}:{SEED}", family=family, adapter_id=adapter_id
    )
    hub.readiness.ready = h.readiness_from(process)
    request_id = h.started_request(hub, h.request_body(capability), capability)
    binding = h.binding_for(request_id, capability, device_id=f"dev_kit_{family}")
    assert h.start_acquisition(process, binding) == "started"
    return hub, process, h.stored_request(hub, request_id)


def _recorded(hub: mh.Hub, stored: dict[str, Any]) -> dict[str, Decimal]:
    assert stored["state"] == "completed"
    (attempt,) = stored["attempts"]
    assert attempt["outcome"] == "success"
    assert {(d.origin_mode, d.acquisition_method) for d in hub.observations.drafts} == {("SIM", "device")}
    return {str(d.code): d.quantity.value for d in hub.observations.drafts}


def _refused(hub: mh.Hub, stored: dict[str, Any]) -> tuple[str, str, set[str]]:
    (attempt,) = stored["attempts"]
    assert attempt["outcome"] != "success"
    assert hub.observations.drafts == []
    return str(attempt["outcome"]), str(attempt["failure_reason"]), set(attempt.get("quality_notes") or ())


@pytest.mark.parametrize("scenario", ["normal", "hypoxia_drop", "malformed_packet"])
def test_oximeter_twin_reading_is_recorded_sim_with_the_steady_spo2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    hub, process, stored = _run(tmp_path, monkeypatch, scenario, family="oximeter", capability="spo2")

    assert _recorded(hub, stored) == {"spo2": _steady_spo2(scenario)}
    (attempt,) = stored["attempts"]
    # No source sets an oximeter rate limit: the check is unset, and the reading says so.
    assert "unchecked:rate:spo2" in attempt["quality_notes"]
    value = _steady_spo2(scenario)
    if scenario == "normal":
        assert Decimal(95) <= value <= Decimal(100)  # "95% to 100% for most healthy individuals" (C08, FDA)
    if scenario == "hypoxia_drop":
        assert value < Decimal(95)
    if scenario == "malformed_packet":
        # The truncated notification is a named frame error, never a reading; the valid frames still count.
        assert "frame:truncated" in attempt["quality_notes"]
    assert process.journal.records() == ()


@pytest.mark.parametrize(
    ("scenario", "outcome", "reason", "notes"),
    [
        ("finger_out", "rejected", "insufficient_samples", {"device_status:sensor_unconnected_to_user"}),
        (
            "motion_low_perfusion",
            "rejected",
            "insufficient_samples",
            {"device_status:erratic_signal", "device_status:questionable_measurement"},
        ),
        ("out_of_range", "rejected", "out_of_device_range", set()),
        ("frozen_sensor", "rejected", "stuck", {"frame:repeated_measurement"}),
    ],
)
def test_oximeter_twin_faults_are_refused_with_their_reason_and_no_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, outcome: str, reason: str, notes: set[str]
) -> None:
    hub, process, stored = _run(tmp_path, monkeypatch, scenario, family="oximeter", capability="spo2")

    refused_outcome, refused_reason, refused_notes = _refused(hub, stored)
    assert (refused_outcome, refused_reason) == (outcome, reason)
    assert notes <= refused_notes
    assert process.health()["state"] == "connected"


@pytest.mark.parametrize("scenario", ["disconnect_mid_reading", "low_battery"])
def test_link_loss_mid_reading_is_unavailable_with_no_value_and_the_capability_goes_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    hub, process, stored = _run(tmp_path, monkeypatch, scenario, family="oximeter", capability="spo2")

    # Four notifications arrived, then the link dropped before the window ended (E07-01, review B1): the
    # reading is unavailable, the partial window never becomes a value, and the capability goes down.
    twin = Twin("oximeter", scenario, SEED, start=h.TWIN_START, interval_s=h.TWIN_INTERVAL_S)
    assert any(isinstance(e, Disconnect) for e in twin.events(WINDOW))
    assert len([n for n in _notifications("oximeter", scenario) if "plx" in n.characteristic]) == 4
    assert _refused(hub, stored)[:2] == ("failed", "disconnected")
    assert process.health()["state"] == "disconnected"
    hub.readiness.ready = h.readiness_from(process)
    capability = h.capability(hub, "spo2")
    assert (capability["status"], capability["unavailable_reason"]) == ("unavailable", "adapter_down")


def test_thermometer_twin_fever_reads_above_its_normal_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readings = {}
    for scenario in ("normal", "fever"):
        run_path = tmp_path / scenario
        run_path.mkdir()
        with monkeypatch.context() as patch:
            hub, _process, stored = _run(
                run_path, patch, scenario, family="thermometer", capability="temperature"
            )
            recorded = _recorded(hub, stored)
        expected = _float(_notifications("thermometer", scenario)[-1].payload[1:5])
        assert expected is not None
        assert recorded == {"temperature": expected}
        readings[scenario] = expected
    assert readings["fever"] > readings["normal"]


@pytest.mark.parametrize(
    ("family", "capability"), [("thermometer", "temperature"), ("cuff", "blood_pressure")]
)
def test_a_spot_device_that_drops_the_link_after_its_measurement_records_that_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family: Family, capability: str
) -> None:
    """Each thermometer 0x2A1C or cuff 0x2A35 indication is a whole measurement (Bluetooth SIG): the device
    ending the connection after one is the normal end of a spot reading (profile completion `measurement`)."""
    hub, _process, stored = _run(
        tmp_path, monkeypatch, "disconnect_mid_reading", family=family, capability=capability
    )

    last = _notifications(family, "disconnect_mid_reading")[-1].payload
    if family == "thermometer":
        assert _recorded(hub, stored) == {"temperature": _float(last[1:5])}
    else:
        assert _recorded(hub, stored) == {
            "systolic_bp": _sfloat(last[1:3]),
            "diastolic_bp": _sfloat(last[3:5]),
        }


@pytest.mark.parametrize(
    ("family", "capability"), [("thermometer", "temperature"), ("cuff", "blood_pressure")]
)
def test_a_frozen_spot_device_is_refused_as_stuck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family: Family, capability: str
) -> None:
    hub, _process, stored = _run(tmp_path, monkeypatch, "frozen_sensor", family=family, capability=capability)

    assert _refused(hub, stored) == ("rejected", "stuck", {"frame:repeated_measurement"})


def test_without_a_sourced_device_range_an_extreme_temperature_is_passed_on_and_marked_unchecked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No catalog source sets a thermometer device range, so the adapter does not invent one: the wire-valid
    value reaches the hub with an unchecked:range note, and the hub's own acceptance decides."""
    hub, _process, stored = _run(
        tmp_path, monkeypatch, "out_of_range", family="thermometer", capability="temperature"
    )

    expected = _float(_notifications("thermometer", "out_of_range")[-1].payload[1:5])
    assert _recorded(hub, stored) == {"temperature": expected}
    (attempt,) = stored["attempts"]
    assert {"unchecked:range:temperature", "unchecked:rate:temperature"} <= set(attempt["quality_notes"])


def test_cuff_twin_hypertension_panel_is_recorded_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, process, stored = _run(
        tmp_path, monkeypatch, "hypertension", family="cuff", capability="blood_pressure"
    )

    last = _notifications("cuff", "hypertension")[-1].payload
    recorded = _recorded(hub, stored)
    assert recorded == {"systolic_bp": _sfloat(last[1:3]), "diastolic_bp": _sfloat(last[3:5])}
    assert recorded["systolic_bp"] > recorded["diastolic_bp"]
    assert process.journal.records() == ()
