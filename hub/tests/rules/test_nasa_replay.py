"""Replay known NASA outcomes through the served hub (AGENTS 4 "Replay known outcomes"; model validation only).

Each test drives recorded or printed NASA data through the adapter ingest operation of a real station
(create_app over real SQLite, the real sensors and rules modules, E23's sourced plausibility) and asserts the
warning the product actually reaches and when, against the documented outcome in docs/product/nasa-data/cases/.
Nothing here is a diagnosis claim, a crew dose or a crew action.

Findings (kept as tests, never deleted or weakened):

- spe-2017-09-iss-columbus and spe-2017-09-mars-surface: the product reaches NO radiation warning from the
  recorded dose rate, while the case expects watch from the first high-latitude passes after 2017-09-11 04:30 UTC
  (ISS, N40 section 3.3) and watch then act for anyone outside on Mars (N39, N40). Three independent causes, each
  asserted below so the test fails the day one of them changes: (1) config/sensor-limits.json `rad_rate_watch`
  and `rad_rate_act` have `value: null`, status `threshold_pending`, because NASA requires the program to
  predefine the alert threshold (EV01, environment-hazards.json radiation levels) and none is sourced; (2) no
  definition in config/warning-rules.json names a `rad` limit; (3) the sensor catalog has no plausibility
  criteria for `rad`, so E23 captures every reading and evaluates none (R-E23-2 covers CO2 only). The data itself
  shows the event (asserted from what the hub stored), so the miss is the product's configuration, not the data.
  The storm path that does work is the mission event `space_weather_alert` at Emergency (D2,
  test_storm_occurrences_served.py), which needs a ground or operator alert, not the dose rate.
- iss-co2-symptom-reports-2001-2009 (qualitative: spot values only, the case says so): Expedition 6 "above 4 mm Hg"
  reaches act, and the documented relief "to the 2 mm Hg range" clears it by the rule (condition_false after the
  pinned dwell on fresh evidence), never by anyone else. Stated assumption, as the case asks: the printed level
  held for one hour (it is a spot value) and the fall to 2 mm Hg follows the steepest change E23 accepts as
  plausible (sensor-catalog co2 rate bound, N02), with a small plausible jitter on held levels because E23 cannot
  prove a constant value is not stuck.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import e23_station as base
import pytest
from e24_station import build, post, wobble

pytestmark = [pytest.mark.E24, pytest.mark.trace("AGENTS-4-replay", "V55", "V56", "R35")]

ROOT = base.ROOT
CASES = ROOT / "docs" / "product" / "nasa-data" / "cases"
ISS_FILE = ROOT / "docs" / "product" / "nasa-data" / "radiation" / "radlab_iss_2017-09_spe.csv"
MARS_DIR = CASES / "spe-2017-09-mars-surface"
CO2_DIR = CASES / "iss-co2-symptom-reports-2001-2009"
BATCH = 200


def _rows(st: base.Station, sql: str, *args: object) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


def _config(name: str) -> dict[str, Any]:
    data = json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _case(directory: Path) -> dict[str, Any]:
    data = json.loads((directory / "outcome.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def _send(st: base.Station, rows: list[dict[str, Any]], prefix: str) -> None:
    for offset in range(0, len(rows), BATCH):
        reply = post(st, rows[offset : offset + BATCH], f"{prefix}_{offset:06d}")
        assert reply.status_code == 200, reply.text


def _radiation_not_configured() -> None:
    """The three causes of the radiation miss, read from the shipped configuration (finding, see docstring)."""
    limits = [lim for lim in _config("sensor-limits.json")["limits"] if lim["sensor_type_id"] == "rad"]
    assert {lim["limit_id"] for lim in limits} == {"rad_rate_watch", "rad_rate_act"}
    assert all(lim["value"] is None and lim["status"] == "threshold_pending" for lim in limits)
    named = {d["condition"].get("limit_id") for d in _config("warning-rules.json")["definitions"]}
    assert not named & {lim["limit_id"] for lim in limits}
    (rad,) = [t for t in _config("sensor-catalog.json")["sensor_types"] if t["id"] == "rad"]
    assert rad.get("plausibility") is None


def _radiation_station(tmp: Path, environment: str) -> base.Station:
    channel = base.channel(
        "ch_rad", "rad", "radiation", "REPLAY", "µGy/h",
        environment_ids=[environment], expected_interval_seconds=None, stale_after_seconds=600,
    )  # fmt: skip
    st = build(tmp, registry={"channels": [channel]})
    st.initialize("SIM", environment)
    st.advance(seconds=60)
    return st


def _radiation_outcome(st: base.Station, sent: int) -> dict[str, Any]:
    stored = _rows(st, "SELECT count(*) FROM sensors_reading WHERE channel_id = 'ch_rad'")[0][0]
    warnings = _rows(st, "SELECT definition_id, level, hazard FROM (SELECT definition_id, level, "
                     "json_extract(body_json, '$.hazard_id') AS hazard FROM rules_warning)")  # fmt: skip
    return {"stored": stored, "sent": sent, "warnings": warnings}


@pytest.mark.trace("T-E24-028", "INV-E24-14")
def test_spe_2017_iss_columbus_dostel2_replay_raises_no_radiation_warning(
    patched: None,
    tmp_path: Path,
) -> None:
    case = _case(CASES / "spe-2017-09-iss-columbus")
    assert case["expected_product_output"]["level"] == "watch"  # what the product should reach
    onset = datetime(2017, 9, 11, 4, 30, tzinfo=UTC)  # N40 section 3.3, PDF page 9, quoted in outcome.json
    assert "11 September 04:30" in case["documented_outcome"][0]["quote"]
    st = _radiation_station(tmp_path, case["environment_id"])
    with ISS_FILE.open(encoding="utf-8", newline="") as handle:
        source = [
            r for r in csv.DictReader(handle)
            if r["instrument_id"] == "DosTel2"
            and datetime(2017, 9, 10, tzinfo=UTC) <= _utc(r["timestamp"]) < onset + timedelta(hours=2)
        ]  # fmt: skip
    rows = [
        base.reading("ch_rad", i + 1, float(r["absorbed_dose_rate"]), "µGy/h", _utc(r["timestamp"]), "REPLAY")
        for i, r in enumerate(source)
    ]
    _send(st, rows, "iss")
    quiet = max(float(r["absorbed_dose_rate"]) for r in source
                if _utc(r["timestamp"]) < onset and abs(float(r["latitude"])) >= 45)  # fmt: skip
    after = max(float(r["absorbed_dose_rate"]) for r in source
                if _utc(r["timestamp"]) >= onset and abs(float(r["latitude"])) >= 45)  # fmt: skip
    assert after > 2 * quiet  # the replayed data carries the documented event (curator method, case file)
    outcome = _radiation_outcome(st, len(rows))
    assert outcome["stored"] == outcome["sent"]  # every reading captured, none discarded
    assert not [w for w in outcome["warnings"] if w[0] != "sensor_quiet"]  # FINDING: no radiation warning
    assert all(level != "emergency" for _, level, _ in outcome["warnings"])  # the case's must_not holds
    _radiation_not_configured()


@pytest.mark.trace("T-E24-028", "INV-E24-14")
def test_spe_2017_mars_surface_rad_replay_raises_no_radiation_warning(patched: None, tmp_path: Path) -> None:
    case = _case(MARS_DIR)
    assert case["expected_product_output"]["levels"][0].startswith("watch")
    st = _radiation_station(tmp_path, case["environment_id"])
    data = MARS_DIR / str(case["input_data"][0]["path"])
    with data.open(encoding="utf-8", newline="") as handle:
        source = [(_utc(r["timestamp"]), float(r["absorbed_dose_rate"])) for r in csv.DictReader(handle)]
    rows = [base.reading("ch_rad", i + 1, v, "µGy/h", at, "REPLAY") for i, (at, v) in enumerate(source)]
    _send(st, rows, "mars")
    pre = [v for at, v in source if at < datetime(2017, 9, 10, 12, tzinfo=UTC)]
    assert max(v for _, v in source) >= 2 * sum(pre) / len(pre)  # N39 "more than double", in the data
    outcome = _radiation_outcome(st, len(rows))
    assert outcome["stored"] == outcome["sent"]
    assert not [w for w in outcome["warnings"] if w[0] != "sensor_quiet"]  # FINDING: neither watch nor act
    assert all(level != "emergency" for _, level, _ in outcome["warnings"])
    _radiation_not_configured()


def _co2_levels() -> tuple[Decimal, Decimal]:
    """Expedition 6 as printed (law2010 p. 8, N38): above 4 mm Hg, relief in the 2 mm Hg range."""
    with (CO2_DIR / "co2_symptom_reports.csv").open(encoding="utf-8", newline="") as handle:
        (row,) = [r for r in csv.DictReader(handle) if r["event"] == "expedition6"]
    relief = re.search(r"relief at about (\d+(?:\.\d+)?) mm Hg", row["co2_as_printed"])
    assert relief is not None
    return Decimal(row["co2_low_mm_hg"]), Decimal(relief.group(1))


def _co2_rate() -> Decimal:
    (co2,) = [t for t in _config("sensor-catalog.json")["sensor_types"] if t["id"] == "co2"]
    return Decimal(str(co2["plausibility"]["rate"]["max_per_second"]))


def _co2_state(st: base.Station) -> tuple[Any, ...] | None:
    found = _rows(st, "SELECT level, state, clear_reason, origin_mode FROM rules_warning "
                      "WHERE definition_id = 'co2_above_limit'")  # fmt: skip
    return found[0] if found else None


def test_iss_co2_expedition6_printed_levels_raise_act_and_the_documented_relief_clears_it(
    patched: None,
    tmp_path: Path,
) -> None:
    """Qualitative case (spot values only). Expected: act while above 4 mm Hg; cleared by the rule after the
    reduction to about 2 mm Hg (case expected_product_output, expedition6)."""
    case = _case(CO2_DIR)
    assert case["case_type"] == "qualitative"
    (expected,) = [e for e in case["expected_product_output"] if e["event"] == "expedition6"]
    high, relief = _co2_levels()
    st = build(tmp_path, registry={"channels": [base.channel("ch_co2", "co2", "co2", "SIM", "mmHg")]})
    st.initialize("SIM")
    st.advance(seconds=3600)
    hour = [base.reading("ch_co2", n + 1, float(high) + wobble("co2", 5, n), "mmHg",
                         st.clock.at - timedelta(seconds=(720 - n) * 5)) for n in range(720)]  # fmt: skip
    _send(st, hour, "exp6_high")
    assert _co2_state(st) == (expected["level"], "raised", None, "SIM")
    raised_at = st.clock.at
    step = _co2_rate() * 5 * Decimal("0.9")  # every 5 s step stays inside the plausible rate
    value, seq, cleared = high, 721, None
    for minute in range(1, 181):  # live feed, one reading every 5 s, posted each minute
        st.advance(seconds=60)
        batch = []
        for k in range(12):
            value = max(relief, value - step)
            jitter = wobble("co2", 5, seq) if value == relief else 0.0  # a held level still moves (R-E23-2)
            batch.append(base.reading("ch_co2", seq, float(value) + jitter, "mmHg",
                                      st.clock.at - timedelta(seconds=55 - 5 * k)))  # fmt: skip
            seq += 1
        reply = post(st, batch, f"exp6_live_{minute:03d}")
        assert reply.status_code == 200, reply.text
        state = _co2_state(st)
        assert state is not None and state[0] == "act"
        if state[1] == "cleared":
            cleared = minute
            break
    assert _co2_state(st) == ("act", "cleared", "condition_false", "SIM")
    assert cleared is not None and st.clock.at - raised_at <= timedelta(hours=3)
