"""R-E23-2 through the served hub with the warnings engine composed (T-E23-025, 029, 070; DE23-11).

create_app over a real on-disk SQLite, the real sensors, mission and rules (E24) modules and the shipped config/
documents; the plausibility gate is NOT patched here, unlike hub/tests/rules/e24_station.py. A REPLAY CO2 hour from
the NASA RR-1 file reaches GET /v1/warnings, and each injected fault (out of range, rate spike, saturated floor,
stuck feed, disconnect) is kept and served flagged but never raises or clears.

Expected numbers come from the source text, never from the criteria under test: the 3 mmHg one-hour limit is
NASA-STD-3001 Vol 2 Rev F [V2 6004] (C25 p. 4 quotes it); the RR-1 hour is read from the CSV here; 760 mmHg is the
assumed cabin pressure NASA-SOURCES.md section 2 states for the ppm file.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import e23_station as base
import pytest
from e23_helpers import read_rr1_ppm
from e23_station import ADAPTER, START, Station, build_station, channel, reading

from crew_health.rules import module as rules_module
from crew_health.rules.settings import DEFAULTS as RULE_DEFAULTS
from crew_health.rules.settings import specifications as rule_specs
from crew_health.sensors import module as sensors_module

pytestmark = [pytest.mark.e23, pytest.mark.trace("T-E23-025", "T-E23-029", "T-E23-070", "R-E23-2")]
PATH = "/v1/adapters/" + ADAPTER + "/readings"
PRESSURE = {
    "value": 760,
    "basis": "assumed",
}  # NASA-SOURCES.md section 2: the RR-1 file has no cabin pressure
V2_6004_MMHG = Decimal(3)  # [V2 6004]: one-hour average ppCO2 no more than 3 mmHg (source text, not config)
DEMO_POINT = "2014-10-07T15:36"  # eda_rr1_provenance.json consumers: the demo replay clock
CADENCE = 300  # the RR-1 file's sample interval (eda_rr1_provenance.json interval_seconds_counts)


class _SensorsAndRules:
    @staticmethod
    def register(registry: Any) -> None:
        sensors_module.register(registry)
        rules_module.register(registry)


@pytest.fixture
def with_rules(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Compose E24 into the E23 station. Only the module list and settings change; nothing is stubbed."""
    original = base.sensor_specs
    monkeypatch.setattr(base, "sensor_specs", lambda: (*original(), *rule_specs()))
    monkeypatch.setattr(base, "SENSOR_DEFAULTS", {**base.SENSOR_DEFAULTS, **RULE_DEFAULTS})
    monkeypatch.setattr(base, "sensors_module", _SensorsAndRules)
    yield


def _station(tmp: Path, origin: str = "SIM") -> Station:
    """One CO2 channel, so the card shows it (DE23-10 ranks channels of a type)."""
    cadence = {
        "expected_interval_seconds": CADENCE,
        "expected_interval_basis": "RR-1 file cadence, N02",
        "stale_after_seconds": 900,
        "stale_after_basis": "three samples, team default",
    }
    st = build_station(
        tmp,
        registry={
            "channels": [
                channel("ch_co2_rr1", "co2", "co2", "REPLAY", "ppm", **cadence)
                if origin == "REPLAY"
                else channel("ch_co2_sim", "co2", "co2", "SIM", "mmHg", **cadence)
            ]
        },
    )
    st.initialize("SIM")
    st.advance(hours=5)  # SIM readings below are observed after START, inside the station clock's known span
    return st


def _post(st: Station, rows: list[Any], tag: str) -> None:
    for offset in range(0, len(rows), 200):
        reply = st.client.post(
            PATH,
            json={"batch_id": f"bat_{tag}_{offset}", "readings": rows[offset : offset + 200]},
            headers=st.headers("adapter", f"key_{tag}_{offset:05d}"),
        )
        assert reply.status_code == 200, reply.text
        assert reply.json()["rejected"] == [], reply.json()  # a plausibility failure is stored, not rejected


def _warnings(st: Station) -> dict[str, dict[str, Any]]:
    reply = st.client.get("/v1/warnings", headers=st.headers("crew_a"))
    assert reply.status_code == 200, reply.text
    return {item["definition_id"]: item for item in reply.json()["items"]}


def _stored(st: Station, channel_id: str) -> list[tuple[str, int, str, str | None]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(
            "SELECT reading_id, adapter_sequence, quality_status, provenance_trace FROM sensors_reading "
            "WHERE channel_id = ? ORDER BY adapter_sequence",
            (channel_id,),
        ).fetchall()
    finally:
        connection.close()


def _served(st: Station, reading_id: str) -> dict[str, Any]:
    reply = st.client.get("/v1/sensor-readings/" + reading_id, headers=st.headers("crew_a"))
    assert reply.status_code == 200, reply.text
    found: dict[str, Any] = reply.json()
    return found


def _status(st: Station) -> dict[str, Any]:
    found: dict[str, Any] = st.client.get("/v1/sensors/co2", headers=st.headers("crew_a")).json()
    return found


def _evaluation(st: Station) -> dict[str, Any]:
    found: dict[str, Any] = _status(st)["evaluation"]
    return found


def _sim(st: Station, sequence: int, value: float, seconds_ago: int) -> dict[str, Any]:
    return reading("ch_co2_sim", sequence, value, "mmHg", st.clock.at - timedelta(seconds=seconds_ago))


def _baseline(st: Station, value: float, count: int) -> list[dict[str, Any]]:
    """`count` SIM readings at the RR-1 cadence, the last one 60 s ago, moving by 0.01 mmHg as a live cell's noise
    does: a perfectly flat feed can never prove it is not stuck, so it is never evaluated (R-E23-2)."""
    return [_sim(st, i + 1, value + 0.01 * (i % 2), 60 + CADENCE * (count - 1 - i)) for i in range(count)]


def test_a_replayed_rr1_co2_hour_is_assessed_evaluated_and_raises_through_get_warnings(
    with_rules: None, tmp_path: Path
) -> None:
    data = read_rr1_ppm()
    end = next(i for i, row in enumerate(data) if row.at.isoformat().startswith(DEMO_POINT))
    window = [r for r in data[: end + 1] if r.at > data[end].at - timedelta(hours=1)]
    expected = sum((r.value for r in window), Decimal(0)) / len(window) * Decimal(760) / Decimal(1_000_000)
    assert len(window) == 12 and expected > V2_6004_MMHG  # the file's own hour, read here: about 3.50 mmHg
    st = _station(tmp_path, "REPLAY")
    rows = [
        reading("ch_co2_rr1", i + 1, float(r.value), "ppm", r.at, "REPLAY", cabin_pressure_mmhg=PRESSURE)
        for i, r in enumerate(data[end - 24 : end + 1])
    ]
    _post(st, rows, "rr1")
    found = _stored(st, "ch_co2_rr1")
    assert len(found) == 25 and found[0][3] == "insufficient_history"  # one reading proves no rate
    assert {q for _, _, q, _ in found} == {"accepted"}  # the whole hour is plausible: nothing flagged
    assert {t for _, _, _, t in found[1:]} <= {
        None,
        "insufficient_history",
    }  # equal neighbours prove no motion
    assert sum(1 for *_, t in found if t is None) >= 12
    evaluation = _evaluation(st)
    assert evaluation["status"] == "beyond_limit" and evaluation["level"] == "act"
    assert abs(Decimal(str(evaluation["aggregated_value"])) - expected) < Decimal("0.001")
    raised = _warnings(st)["co2_above_limit"]
    assert (raised["level"], raised["state"], raised["origin_mode"]) == ("act", "raised", "REPLAY")


def test_an_impossible_high_reading_is_kept_flagged_and_never_raises(
    with_rules: None, tmp_path: Path
) -> None:
    """Thirteen readings at 2 mmHg and one above the sourced range. Had it counted, the one-hour mean would be
    above 3: an impossible value is rejected, kept as measured and never evaluated."""
    st = _station(tmp_path)
    _post(st, _baseline(st, 2.0, 13), "base")
    assert "co2_above_limit" not in _warnings(st)
    st.advance(seconds=CADENCE)
    _post(st, [_sim(st, 14, 800.0, 60)], "fault")
    reading_id, _, quality, trace = _stored(st, "ch_co2_sim")[-1]
    assert (quality, trace) == ("rejected", "out_of_range")
    shown = _served(st, reading_id)
    assert shown["quality_status"] == "rejected" and shown["provenance"]["trace"] == "out_of_range"
    assert shown["value"] == 800.0  # kept as measured, never discarded or clipped
    assert "co2_above_limit" not in _warnings(st)
    assert _evaluation(st)["status"] == "within_limit"


def test_a_rate_spike_is_kept_as_suspect_and_may_raise(with_rules: None, tmp_path: Path) -> None:
    """A jump faster than the sourced rate could be a real event (a scrubber failure), so it is never hidden: it is
    stored uncertain with its reason, shown flagged, and its one-hour mean (above 3 mmHg) raises the act warning."""
    st = _station(tmp_path)
    _post(st, _baseline(st, 2.0, 13), "base")
    st.advance(seconds=CADENCE)
    _post(st, [_sim(st, 14, 30.0, 60)], "fault")
    reading_id, _, quality, trace = _stored(st, "ch_co2_sim")[-1]
    assert (quality, trace) == ("uncertain", "rate_exceeded")
    shown = _served(st, reading_id)
    assert shown["quality_status"] == "uncertain" and shown["provenance"]["trace"] == "rate_exceeded"
    assert shown["value"] == 30.0
    raised = _warnings(st)["co2_above_limit"]
    assert (raised["level"], raised["state"]) == ("act", "raised")


def test_a_saturated_floor_is_kept_flagged_and_never_clears_an_active_warning(
    with_rules: None, tmp_path: Path
) -> None:
    st = _station(tmp_path)
    _post(st, _baseline(st, 4.0, 13), "base")
    raised = _warnings(st)["co2_above_limit"]
    assert raised["state"] == "raised"
    st.advance(seconds=CADENCE * 15)
    floor = [
        _sim(st, 14 + i, 0.0, 60 + CADENCE * (13 - i)) for i in range(14)
    ]  # an hour more, all at the floor
    _post(st, floor, "floor")
    flagged = _stored(st, "ch_co2_sim")[13:]
    assert {(q, t) for _, _, q, t in flagged} == {("rejected", "saturated")} and len(flagged) == 14
    assert _served(st, flagged[-1][0])["quality_status"] == "rejected"
    still = _warnings(st)["co2_above_limit"]
    assert (still["warning_id"], still["state"]) == (raised["warning_id"], "raised")


def test_a_stuck_feed_is_flagged_suspect_from_the_sourced_count(with_rules: None, tmp_path: Path) -> None:
    """Ten moving readings, then the sensor holds 2.3 mmHg for 45 readings 300 s apart. The NASA RR-1/RR-5 files'
    longest natural run is 37 samples (recomputed from the CSVs in test_plausibility), so the 38th identical sample
    onward is flagged."""
    st = _station(tmp_path)
    moving = _baseline(st, 2.0, 55)[:10]
    held = [_sim(st, 11 + i, 2.3, 60 + CADENCE * (44 - i)) for i in range(45)]
    _post(st, moving + held, "stuck")
    found = _stored(st, "ch_co2_sim")
    assert [s for _, s, q, t in found if (q, t) == ("uncertain", "stuck_value")] == list(range(48, 56))
    assert all(t is None for _, s, _, t in found[1:47])  # moving, then a run still short of the count
    assert _served(st, found[-1][0])["provenance"]["trace"] == "stuck_value"
    status = _status(st)  # a suspect reading is a real measurement: it is shown, flagged, and may only raise
    latest = status["latest_reading"]
    assert (latest["value"], latest["quality_status"], latest["provenance"]["trace"]) == (
        2.3,
        "uncertain",
        "stuck_value",
    )
    assert status["evaluation"]["status"] == "within_limit"
    assert "co2_above_limit" not in _warnings(st)


def test_a_disconnect_goes_stale_keeps_the_warning_and_the_next_reading_is_reassessed(
    with_rules: None, tmp_path: Path
) -> None:
    st = _station(tmp_path)
    _post(st, _baseline(st, 4.0, 13), "base")
    raised = _warnings(st)["co2_above_limit"]
    st.advance(hours=2)  # the link drops: no reading for two hours, far past stale_after (900 s)
    st.sweep()
    assert _status(st)["reading_state"] == "stale" and _status(st)["stale_since"] is not None
    still = _warnings(st)["co2_above_limit"]
    assert (still["warning_id"], still["state"]) == (raised["warning_id"], "raised")  # absence never clears
    _post(st, [reading("ch_co2_sim", 14, 2.0, "mmHg", st.clock.at - timedelta(seconds=5))], "back")
    _, _, quality, trace = _stored(st, "ch_co2_sim")[-1]
    assert (quality, trace) == ("accepted", None)  # 2 mmHg over two hours is well inside the sourced rate


def test_a_reading_from_before_activation_is_kept_and_the_batch_still_succeeds_with_warnings_composed(
    with_rules: None, tmp_path: Path
) -> None:
    st = _station(tmp_path)  # the environment is activated at START by initialize()
    _post(st, [reading("ch_co2_sim", 1, 2.0, "mmHg", START - timedelta(hours=1))], "early")
    assert [(q, t) for _, _, q, t in _stored(st, "ch_co2_sim")] == [("accepted", "insufficient_history")]
