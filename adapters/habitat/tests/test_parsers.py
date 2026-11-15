"""Parser tests: recorded CSV, Bluetooth Environmental Sensing and XTCE/CCSDS inputs, the plausibility guard and
the feed documents (T-E23-029 parser half, T-E23-074, T-E23-084 adapter half, R-E23-2 interim).

Hypothesis properties run 1000 examples per the E23 plan. Expected outcomes come from independent reads of the
NASA files (csv module, struct), never from the code under test."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import struct
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from support import DEMO, NASA, SIM_CONFIG, FakeClock, feed_by_id, scratch_feeds

from crew_health_adapter_habitat.ble_ess import (
    ESS_CHARACTERISTICS,
    DeviceIdentity,
    EssSource,
    Notification,
    decode,
    origin_of,
)
from crew_health_adapter_habitat.errors import (
    FeedInvalid,
    LiveRefused,
    ParseRejected,
    ProfileRefused,
    SourceUnavailable,
)
from crew_health_adapter_habitat.feeds import Columns, Feed, Pace, load_feeds, select_feed
from crew_health_adapter_habitat.pipeline import wire_row
from crew_health_adapter_habitat.ports import (
    CabinPressure,
    Criteria,
    Guard,
    Settings,
    SourceItem,
    parse_decimal,
    utc_text,
)
from crew_health_adapter_habitat.replay import CsvReplaySource, parse_dataset_time
from crew_health_adapter_habitat.sim import (
    FrozenSource,
    GeneratorSource,
    TwinBleLink,
    TwinWorld,
    build_sim_source,
    load_twin_world,
)
from crew_health_adapter_habitat.xtce import XtceSource, load_xtce, parse_packet
from crew_health_sim_habitat.packets import PacketTwin, space_packet
from crew_health_sim_habitat.runner import Tick

pytestmark = pytest.mark.e23
EXAMPLES = settings(max_examples=1000, deadline=None)
SETTINGS = Settings()


def csv_feed(path: Path, *, carry: bool = True, row_filter: Any = None) -> Feed:
    return Feed(
        feed_id="f", kind="replay", adapter_id="adp_x", channel_id="ch_x", unit="ppm", pace=Pace("burst"),
        replay=None, file=path, columns=Columns("t", "v", "ppm"), row_filter=row_filter, carry_naive=carry,
        window_from=None, window_to=None, sim=None, cabin_pressure=None, criteria=None, basis={},
    )  # fmt: skip


def parse_csv_text(text: str, *, carry: bool = True, settings_: Settings = SETTINGS) -> list[SourceItem]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "x.csv"
        path.write_text(text, encoding="utf-8", newline="")
        return list(CsvReplaySource(csv_feed(path, carry=carry), settings_).items(0))


cells = st.one_of(
    st.decimals(allow_nan=True, allow_infinity=True).map(str),
    st.text(alphabet="abc -.,+eE0123456789\"'", max_size=12),
    st.just(""),
)
times = st.one_of(
    st.datetimes(min_value=datetime(1990, 1, 1), max_value=datetime(2100, 1, 1)).map(
        lambda d: d.isoformat(sep=" ")
    ),
    st.text(alphabet="0123456789-: TZ+", max_size=24),
)


@pytest.mark.trace("T-E23-074", "AGENTS-5")
@EXAMPLES
@given(rows=st.lists(st.tuples(times, cells), max_size=30))
def test_csv_rows_are_parsed_exactly_or_rejected_and_never_substituted(rows: list[tuple[str, str]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["t", "v"])
    writer.writerows(rows)
    items = parse_csv_text(buffer.getvalue())
    assert [i.position for i in items] == list(range(1, len(rows) + 1))  # every row accounted for, in order
    for item, (t, v) in zip(items, rows, strict=True):
        if item.row is None:
            assert item.reject_code in {"MALFORMED_ROW", "BAD_TIME", "BAD_VALUE", "ROW_TOO_LARGE"}
            continue
        assert item.row.quantity.value == Decimal(v.strip()) and v.strip() != ""  # the original text, exactly
        assert item.row.quantity.value.is_finite() and item.row.quantity.unit == "ppm"
        assert item.row.observed_at.tzinfo is not None
        assert parse_dataset_time(t, carry_naive=True) == item.row.observed_at


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(value=st.sampled_from(["nan", "NaN", "inf", "-inf", "Infinity", "", " ", "1e999999", "--1", "1,2"]))
def test_nonfinite_empty_and_junk_values_never_become_numbers(value: str) -> None:
    items = parse_csv_text(f't,v\n2014-09-20 03:59:00,"{value}"\n')
    assert items[0].row is None and items[0].reject_code in {"BAD_VALUE", "MALFORMED_ROW"}
    assert parse_decimal(value) is None


@pytest.mark.trace("T-E23-074")
def test_empty_oversize_short_and_zone_free_rows_are_rejected_not_filled() -> None:
    assert parse_csv_text("t,v\n") == []
    small = Settings(row_max_bytes=22)
    items = parse_csv_text(
        "t,v\n2014-09-20 03:59:00," + "9" * 40 + "\n2014-09-20 04:00:00,5\n", settings_=small
    )
    assert [i.reject_code for i in items] == ["ROW_TOO_LARGE", None]
    assert [i.reject_code for i in parse_csv_text("t,v\n2014-09-20 03:59:00\n,5\n")] == [
        "MALFORMED_ROW",
        "BAD_TIME",
    ]
    refused = parse_csv_text(
        "t,v\n2014-09-20 03:59:00,5\n", carry=False
    )  # no declared rule: never assume UTC
    assert refused[0].reject_code == "BAD_TIME"
    zoned = parse_csv_text("t,v\n2014-09-20T05:59:00+02:00,5\n", carry=False)
    assert zoned[0].row is not None and zoned[0].row.observed_at == datetime(2014, 9, 20, 3, 59, tzinfo=UTC)


@EXAMPLES
@given(moment=st.datetimes(min_value=datetime(1970, 1, 1), max_value=datetime(2200, 1, 1)))
def test_dataset_time_round_trips_to_the_wire_instant(moment: datetime) -> None:
    parsed = parse_dataset_time(moment.isoformat(), carry_naive=True)
    assert parsed is not None and parsed.replace(tzinfo=None) == moment
    assert (
        utc_text(parsed).endswith("Z") and parse_dataset_time(utc_text(parsed), carry_naive=False) == parsed
    )


def independent_rows(
    path: Path, *, column: str, where: tuple[str, str] | None = None
) -> list[tuple[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        first = handle.readline().strip().split(",")
        handle.seek(0)
        time_col = first[0].strip('"')
        found = [r for r in csv.DictReader(handle) if where is None or r[where[0]] == where[1]]
    return [(r[time_col], r[column]) for r in found]


@pytest.mark.trace("T029", "R42", "D01")
def test_the_rr1_file_replays_with_original_dates_and_exact_values() -> None:
    feed = feed_by_id("feed_rr1_co2_replay")
    items = list(CsvReplaySource(feed, SETTINGS).items(0))
    expected = [
        (t, v)
        for t, v in independent_rows(DEMO / "eda_rr1_telemetry.csv", column="co2_iss")
        if "2014-09-30 15:41:00" <= t <= "2014-10-07 15:36:00"
    ]
    assert len(items) == len(expected) == 2012
    assert items[0].row is not None and items[-1].row is not None
    assert utc_text(items[0].row.observed_at) == "2014-09-30T15:45:00Z"  # the original date, not today
    assert utc_text(items[-1].row.observed_at) == "2014-10-07T15:36:00Z"
    assert [str(i.row.quantity.value) for i in items if i.row] == [v for _, v in expected]
    row = wire_row(feed, "REPLAY", 7, items[0].row.quantity.value, items[0].row.observed_at, "not_assessed")
    assert row["origin_mode"] == "REPLAY" and row["unit"] == "ppm"
    assert row["replay"] == {"dataset": feed.replay.dataset, "time_basis": feed.replay.time_basis}  # type: ignore[union-attr]
    assert row["cabin_pressure_mmhg"] == {"value": 760.0, "basis": "assumed"}


@pytest.mark.trace("T029", "T-E23-036")
def test_golden_rr1_window_and_dostel_window_match_the_design_arithmetic() -> None:
    """E23 section 8 golden cases 1 to 3 and 7, recomputed here straight from the file."""
    items = list(CsvReplaySource(feed_by_id("feed_rr1_co2_replay"), SETTINGS).items(0))
    window = [
        i.row.quantity.value
        for i in items
        if i.row
        and datetime(2014, 10, 7, 14, 36, tzinfo=UTC)
        < i.row.observed_at
        <= datetime(2014, 10, 7, 15, 36, tzinfo=UTC)
    ]
    assert len(window) == 12
    mean = sum(window) / len(window)
    assert round(mean, 2) == Decimal("4605.67")
    assert round(mean / 1_000_000 * 760, 4) == Decimal("3.5003")  # ppm to mmHg at the assumed 760 mmHg
    dos = list(CsvReplaySource(feed_by_id("feed_dostel1_rad_replay"), SETTINGS).items(0))
    assert dos[-1].row is not None and utc_text(dos[-1].row.observed_at) == "2022-04-02T01:04:40Z"
    two_hours = [
        i.row.quantity.value
        for i in dos
        if i.row and i.row.observed_at > datetime(2022, 4, 1, 23, 4, 40, tzinfo=UTC)
    ]
    assert len(two_hours) == 80 and round(sum(two_hours) / 80, 4) == Decimal("6.0819")
    assert len(dos) == len(
        independent_rows(
            DEMO / "radlab_dostel_sample.csv", column="absorbed_dose_rate", where=("instrument_id", "DosTel1")
        )
    )


@pytest.mark.trace("T029", "T-E23-050")
def test_a_feed_edit_changes_what_is_parsed_and_served_with_no_code_change(tmp_path: Path) -> None:
    def edit(document: dict[str, Any]) -> None:
        feed = document["feeds"][0]
        feed["columns"] = {"time": "time", "value": "temperature_iss", "unit": "Cel"}
        feed["time_basis"] = "A scratch basis, edited"
        feed["pace"] = {"mode": "fixed", "interval_s": 2}
        del feed["cabin_pressure"]
        feed["from"], feed["to"] = "2014-10-07T15:31:00", "2014-10-07T15:36:00"

    edited = load_feeds(scratch_feeds(tmp_path, edit), profile="demo")[0]
    items = list(CsvReplaySource(edited, SETTINGS).items(0))
    values = [str(i.row.quantity.value) for i in items if i.row]
    assert values == [
        v
        for t, v in independent_rows(DEMO / "eda_rr1_telemetry.csv", column="temperature_iss")
        if "2014-10-07 15:31:00" <= t <= "2014-10-07 15:36:00"
    ]
    row = wire_row(edited, "REPLAY", 0, items[0].row.quantity.value, items[0].row.observed_at, "not_assessed")  # type: ignore[union-attr]
    assert (
        row["unit"] == "Cel"
        and row["replay"]["time_basis"] == "A scratch basis, edited"
        and "cabin_pressure_mmhg" not in row
    )
    assert edited.pace == Pace("fixed", 2.0, 0.0)


@pytest.mark.trace("T-E23-084")
def test_feeds_refuse_outside_the_demo_profile_and_when_invalid(tmp_path: Path) -> None:
    for profile in (None, "", "production", "Demo"):
        with pytest.raises(ProfileRefused):
            load_feeds(DEMO / "feeds.json", profile=profile)
    for edit in (
        lambda d: d["feeds"][0].update(unknown=1),
        lambda d: d["feeds"][0].pop("basis"),
        lambda d: d["feeds"][0].update(file="../escape.csv"),
        lambda d: d["feeds"].append(dict(d["feeds"][0])),  # duplicate feed id
        lambda d: d.update(profile="live"),
    ):
        with pytest.raises(FeedInvalid):
            load_feeds(scratch_feeds(tmp_path / str(id(edit)), edit), profile="demo")
    broken = tmp_path / "feeds.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(FeedInvalid):
        load_feeds(broken, profile="demo")
    with pytest.raises(FeedInvalid):
        select_feed(
            load_feeds(DEMO / "feeds.json", profile="demo"), "replay", None
        )  # two replay feeds: name one


@pytest.mark.trace("T029", "R-E23-15")
def test_the_demo_files_are_byte_copies_of_the_nasa_files_with_provenance_and_valid_stale_times() -> None:
    sums = {
        "eda_rr1_telemetry.csv": NASA / "habitat-environment" / "eda_rr1_telemetry.csv",
        "radlab_dostel_sample.csv": NASA / "radiation" / "radlab_dostel_sample.csv",
    }
    provenance = json.loads((DEMO / "provenance.json").read_text(encoding="utf-8"))
    for name, source in sums.items():
        assert (DEMO / name).read_bytes() == source.read_bytes()
        stated = next(f["sha256"] for f in provenance["files"] if f["file"] == name)
        assert hashlib.sha256((DEMO / name).read_bytes()).hexdigest() == stated
    assert all(
        "timezone" in provenance["dataset_clock"][n] or "not" in provenance["dataset_clock"][n] for n in sums
    )
    channels = json.loads((DEMO / "sensor-channels.json").read_text(encoding="utf-8"))["channels"]
    feeds = {f.channel_id for f in load_feeds(DEMO / "feeds.json", profile="demo")}
    assert feeds == {c["channel_id"] for c in channels}
    for channel in channels:
        interval = channel["expected_interval_seconds"]
        assert interval is None or channel["stale_after_seconds"] > interval  # the hub check rejects <=
        if channel["origin_mode"] == "REPLAY":
            assert channel["dataset"]
    by_id = {c["channel_id"]: c for c in channels}
    assert (
        by_id["ch_co2_cabin_replay"]["stale_after_seconds"] == 600
        and by_id["ch_co2_cabin_replay"]["expected_interval_seconds"] == 300
    )
    health_urls = {c["health_url"] for c in channels}
    assert all(
        u.startswith("http://127.0.0.1:81") for u in health_urls
    )  # loopback, adapter port range 8101 to 8109


# ----- Bluetooth Environmental Sensing -------------------------------------------------------------------------


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(uuid=st.sampled_from(sorted(ESS_CHARACTERISTICS)), payload=st.binary(max_size=6))
def test_ble_payloads_decode_exactly_or_are_rejected_with_a_code(uuid: int, payload: bytes) -> None:
    spec = ESS_CHARACTERISTICS[uuid]
    try:
        value = decode(spec, payload)
    except ParseRejected as rejected:
        assert rejected.code in {"BAD_LENGTH", "NOT_KNOWN", "SATURATED", "OUT_OF_RANGE"}
        return
    assert len(payload) == spec.size
    assert value.is_finite()
    raw = int.from_bytes(payload, "little", signed=spec.fmt == "sint16")
    assert value == Decimal(raw) * spec.resolution
    assert int.from_bytes(payload, "little") not in {spec.unknown_raw, spec.saturated_raw}


# Worked by hand from the GSS (version date 2026-09-09), not from the parser table or the simulator's encoder:
# raw = value / 10^d, little endian. Temperature, Section 3.235, Table 3.361: 23.45 degC = 2345 = 0x0929;
# -273.15 = -27315 = 0x954D as two's complement; 327.67 = 0x7FFF; 0x8000 is not known. Humidity, Section 3.134,
# Table 3.226: 100.00 % = 10000 = 0x2710, 0.00 % = 0, 45.67 % = 4567 = 0x11D7; 0xFFFF is not known and every
# other value is prohibited. Pressure, Section 3.195, Table 3.303: 101325 Pa = 1013250 = 0x000F7602. CO2,
# Section 3.54, Table 3.94: 2650 ppm = 0x0A5A; allowed 0 to 65533; 0xFFFE saturated; 0xFFFF not known.
GSS_VALUES = [
    (0x2A6E, b"\x29\x09", "23.45"), (0x2A6E, b"\x4d\x95", "-273.15"), (0x2A6E, b"\xff\x7f", "327.67"),
    (0x2A6F, b"\x10\x27", "100.00"), (0x2A6F, b"\x00\x00", "0.00"), (0x2A6F, b"\xd7\x11", "45.67"),
    (0x2A6D, b"\x02\x76\x0f\x00", "101325.0"), (0x2A6D, b"\xff\xff\xff\xff", "429496729.5"),
    (0x2B8C, b"\x5a\x0a", "2650"), (0x2B8C, b"\xfd\xff", "65533"),
]  # fmt: skip
GSS_REJECTED = [
    (0x2A6E, b"\x00\x80", "NOT_KNOWN"), (0x2A6E, b"\x4c\x95", "OUT_OF_RANGE"),  # below -273.15
    (0x2A6F, b"\xff\xff", "NOT_KNOWN"), (0x2A6F, b"\x11\x27", "OUT_OF_RANGE"),  # 100.01 %
    (0x2B8C, b"\xfe\xff", "SATURATED"), (0x2B8C, b"\xff\xff", "NOT_KNOWN"),
    (0x2A6F, b"\x10", "BAD_LENGTH"), (0x2A6D, b"\x01\x02", "BAD_LENGTH"), (0x2A6E, b"\x01\x02\x03", "BAD_LENGTH"),
]  # fmt: skip


@pytest.mark.trace("T-E23-074")
def test_the_characteristic_table_decodes_the_values_worked_by_hand_from_the_gss() -> None:
    for uuid, payload, expected in GSS_VALUES:
        assert decode(ESS_CHARACTERISTICS[uuid], payload) == Decimal(expected), (hex(uuid), payload)
    assert {u: (s.fmt, s.resolution, s.unit) for u, s in ESS_CHARACTERISTICS.items()} == {
        0x2A6E: ("sint16", Decimal("0.01"), "Cel"),
        0x2A6F: ("uint16", Decimal("0.01"), "%"),
        0x2A6D: ("uint32", Decimal("0.1"), "Pa"),
        0x2B8C: ("uint16", Decimal(1), "ppm"),
    }
    assert all(
        "GSS Section" in s.source and "AN Section 3.8.2" in s.source for s in ESS_CHARACTERISTICS.values()
    )


@pytest.mark.trace("T-E23-074")
def test_codes_the_gss_reserves_or_prohibits_are_rejected_never_turned_into_numbers() -> None:
    for uuid, payload, code in GSS_REJECTED:
        with pytest.raises(ParseRejected) as caught:
            decode(ESS_CHARACTERISTICS[uuid], payload)
        assert caught.value.code == code, (hex(uuid), payload)


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(raw=st.integers(0, 10000))
def test_humidity_round_trips_inside_its_allowed_range(raw: int) -> None:
    assert decode(ESS_CHARACTERISTICS[0x2A6F], struct.pack("<H", raw)) == Decimal(raw) * Decimal("0.01")


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(raw=st.integers(0, 0xFFFD))
def test_co2_round_trips_below_the_saturation_code(raw: int) -> None:
    assert decode(ESS_CHARACTERISTICS[0x2B8C], struct.pack("<H", raw)) == Decimal(raw)


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(raw=st.integers(-27315, 32767))
def test_temperature_round_trips_inside_its_allowed_range(raw: int) -> None:
    assert decode(ESS_CHARACTERISTICS[0x2A6E], struct.pack("<h", raw)) == Decimal(raw) * Decimal("0.01")


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(raw=st.integers(0, 2**32 - 1))
def test_pressure_round_trips_over_its_whole_type(raw: int) -> None:
    assert decode(ESS_CHARACTERISTICS[0x2A6D], struct.pack("<I", raw)) == Decimal(raw) * Decimal("0.1")


def test_origin_comes_from_the_device_identity_and_live_needs_sourced_criteria() -> None:
    sim = DeviceIdentity("twin", "SIM-1", simulated=True)
    real = DeviceIdentity("sensor", "AA:BB", simulated=False)
    assert origin_of(sim, None) == "SIM"
    with pytest.raises(LiveRefused):
        origin_of(real, None)
    assert origin_of(real, Criteria(basis="a sourced basis")) == "LIVE"


def drain(source: Any) -> list[SourceItem]:
    return list(source.items(0))


ESS_SCENARIO = "ess_link_and_byte_faults"
# Device outputs each characteristic mirrors, and the factor from the device's unit to the characteristic's.
ESS_REGISTERS = {
    0x2A6E: ("sim_temp_rh_cabin", "temperature", 1.0),
    0x2A6F: ("sim_temp_rh_cabin", "humidity", 1.0),
    0x2A6D: ("sim_baro_cabin", "pressure", 1000.0),  # kPa to Pa
}


@pytest.fixture(scope="module")
def world() -> TwinWorld:
    return load_twin_world(SIM_CONFIG)


@dataclass
class Recorded:
    """What a run saw: each notification with the tick time it arrived at, and each device register."""

    notes: list[tuple[float, Notification]]
    registers: dict[int, dict[float, float]]
    gaps: int


def run_link(world: TwinWorld, scenario_id: str, uuids: tuple[int, ...]) -> Recorded:
    """Drive the twin through the BleLink port, reconnecting after each lost link until the run ends."""
    scenario, faults = world.run(scenario_id)
    now = [0.0]
    registers: dict[int, dict[float, float]] = {u: {} for u in uuids}

    def ticks() -> Iterator[Tick]:
        for tick in world.ticks(scenario):
            now[0] = tick.t_s
            for r in tick.readings:
                for uuid in uuids:
                    device, output, factor = ESS_REGISTERS[uuid]
                    if (r.device_id, r.output) == (device, output):
                        registers[uuid][tick.t_s] = r.quantity.value * factor
            yield tick

    link = TwinBleLink(world.ess, faults, ticks(), uuids)
    assert link.identity().simulated is True  # declares itself simulated, so the hub labels it SIM
    notes: list[tuple[float, Notification]] = []
    gaps = 0
    while True:
        try:
            notes.extend((now[0], n) for n in link.notifications())
            break
        except SourceUnavailable:
            gaps += 1
    link.close()
    return Recorded(notes, registers, gaps)


@pytest.fixture(scope="module")
def faulted(world: TwinWorld) -> Recorded:
    return run_link(world, ESS_SCENARIO, tuple(ESS_REGISTERS))


def window(world: TwinWorld, fault: str) -> tuple[float, float]:
    item = next(f for f in world.ess.scenarios[ESS_SCENARIO].faults if f.fault == fault)
    return item.start_s, item.start_s + item.duration_s


def decoded(rec: Recorded, uuid: int) -> list[tuple[float, Decimal | str]]:
    """Each notification of one characteristic through the real parser: a value or its reject code."""
    out: list[tuple[float, Decimal | str]] = []
    for t_s, note in rec.notes:
        if note.uuid16 == uuid:
            try:
                out.append((t_s, decode(ESS_CHARACTERISTICS[uuid], note.payload)))
            except ParseRejected as rejected:
                out.append((t_s, rejected.code))
    return out


@pytest.mark.trace("T-E23-074", "T-E23-062")
def test_the_real_ess_parser_decodes_the_twin_bytes_to_the_device_registers(world: TwinWorld) -> None:
    scenario = replace(world.scenarios["station_nominal"], duration_s=1800.0)
    ticks = list(world.ticks(scenario))
    for uuid, (device, output, factor) in ESS_REGISTERS.items():
        link = TwinBleLink(world.ess, (), iter(ticks), (uuid,))
        items = drain(EssSource(link, "ch", uuid, FakeClock()))
        expected = [
            r.quantity.value * factor
            for t in ticks
            for r in t.readings
            if (r.device_id, r.output) == (device, output)
        ]
        resolution = float(ESS_CHARACTERISTICS[uuid].resolution)
        assert items
        assert len(items) == len(expected)
        for item, register in zip(items, expected, strict=True):
            assert item.row is not None, (hex(uuid), item.reject_code)
            assert float(item.row.quantity.value) == pytest.approx(register, abs=resolution / 2 + 1e-9)
            assert item.row.quantity.unit == ESS_CHARACTERISTICS[uuid].unit


@pytest.mark.trace("T-E23-062", "AGENTS-3")
def test_a_lost_link_stops_notifications_and_a_reconnect_resumes_them_with_a_gap(
    world: TwinWorld, faulted: Recorded
) -> None:
    start, end = window(world, "disconnect")
    assert faulted.gaps == 1
    times = sorted({t for t, _ in faulted.notes})
    assert not [t for t in times if start <= t < end]  # nothing is sent while the link is down
    assert max(t for t in times if t < start) >= start - 60.0
    assert min(t for t in times if t >= start) >= end  # and nothing is invented after it


@pytest.mark.trace("T-E23-062", "AGENTS-5")
def test_a_prohibited_humidity_code_reaches_the_parser_as_out_of_range(
    world: TwinWorld, faulted: Recorded
) -> None:
    start, end = window(world, "out_of_range")
    humidity = decoded(faulted, 0x2A6F)
    inside = [v for t, v in humidity if start <= t < end]
    assert inside
    assert set(inside) == {"OUT_OF_RANGE"}  # 104.50 % is prohibited by the GSS; no number comes out
    assert all(isinstance(v, Decimal) for t, v in humidity if not start <= t < end)


@pytest.mark.trace("T-E23-062", "AGENTS-5")
def test_a_stuck_temperature_repeats_while_the_register_moves(world: TwinWorld, faulted: Recorded) -> None:
    start, end = window(world, "stuck")
    temperature = decoded(faulted, 0x2A6E)
    held = [v for t, v in temperature if start <= t < end]
    assert len(held) > 5
    assert len(set(held)) == 1
    register = [r for t, r in faulted.registers[0x2A6E].items() if start <= t < end]
    assert max(register) - min(register) > 0.0  # the device moved; the bytes did not


@pytest.mark.trace("T-E23-062", "AGENTS-5")
def test_a_drifting_pressure_gains_its_rate_on_the_wire(world: TwinWorld, faulted: Recorded) -> None:
    fault = next(f for f in world.ess.scenarios[ESS_SCENARIO].faults if f.fault == "drift")
    assert fault.value == 600.0  # Pa per hour, from the twin's scenario
    start, end = window(world, "drift")
    seen = 0
    for t, value in decoded(faulted, 0x2A6D):
        assert isinstance(value, Decimal)
        register = faulted.registers[0x2A6D][t]
        excess = float(value) - register
        if start <= t < end:
            seen += 1
            assert excess == pytest.approx(fault.value * (t - start) / 3600.0, abs=0.06)
        else:
            assert excess == pytest.approx(0.0, abs=0.06)
    assert seen > 100


@pytest.mark.trace("T-E23-062", "AGENTS-4")
def test_the_ess_source_labels_positions_and_reject_codes_for_the_fault_run(world: TwinWorld) -> None:
    scenario, faults = world.run(ESS_SCENARIO)
    link = TwinBleLink(world.ess, faults, world.ticks(scenario), (0x2A6F,))
    source = EssSource(link, "ch", 0x2A6F, FakeClock())
    items: list[SourceItem] = []
    with pytest.raises(SourceUnavailable):
        for item in source.items(0):
            items.append(item)
    assert [i.position for i in items] == list(range(1, len(items) + 1))
    assert all(i.row is not None for i in items)  # before the disconnect every humidity byte is a value
    resumed = list(EssSource(link, "ch", 0x2A6F, FakeClock()).items(len(items)))
    assert resumed[0].position == len(items) + 1
    assert "OUT_OF_RANGE" in {i.reject_code for i in resumed}


def test_a_twin_scenario_or_characteristic_that_does_not_exist_is_refused() -> None:
    base = {"type": "twin", "protocol": "ble_ess", "scenario": "station_nominal", "select": "0x2A6E"}
    config_dir = str(SIM_CONFIG)
    build = build_sim_source
    clock, epoch = FakeClock(), datetime(2026, 1, 1, tzinfo=UTC)
    assert build(sim_feed({**base, "config_dir": config_dir}), clock, epoch) is not None
    for edit in ({"scenario": "nope"}, {"protocol": "xtce", "select": "nope"}, {"config_dir": str(DEMO)}):
        with pytest.raises(FeedInvalid):
            build(sim_feed({**base, "config_dir": config_dir, **edit}), clock, epoch)
    with pytest.raises(ParseRejected):  # a characteristic the table does not have
        build(sim_feed({**base, "config_dir": config_dir, "select": "0x1234"}), clock, epoch)


@pytest.mark.trace("T-E23-062", "AGENTS-2")
def test_guard_flags_each_fault_with_scratch_criteria_and_never_invents_thresholds() -> None:
    scratch = Criteria(  # test scratch criteria: they exercise the mechanism and are not a clinical limit
        basis="test scratch", minimum=Decimal(0), maximum=Decimal(20000), saturation=(Decimal(65534),),
        stuck_count=4, stuck_span_s=Decimal(30), max_rate_per_s=Decimal(500),
    )  # fmt: skip
    start = datetime(2026, 1, 1, tzinfo=UTC)

    def verdicts(values: list[Decimal], step_s: int = 10, criteria: Criteria | None = scratch) -> list[Any]:
        guard = Guard(criteria)
        return [guard.assess(v, start + timedelta(seconds=step_s * i)) for i, v in enumerate(values)]

    assert {v.quality for v in verdicts([Decimal(5)] * 3, criteria=None)} == {
        "not_assessed"
    }  # no criteria: flagged
    ok = verdicts([Decimal(2600 + i) for i in range(6)])
    assert ok[0].quality == "uncertain" and ok[-1].quality == "accepted"  # history first, then proven
    assert verdicts([Decimal(-1)])[0].flags == ("BELOW_RANGE",)
    assert "ABOVE_RANGE" in verdicts([Decimal(60000)])[0].flags
    assert "SATURATED" in verdicts([Decimal(65534)])[0].flags
    stuck = verdicts([Decimal(2600)] * 6)
    assert stuck[-1].quality == "rejected" and "STUCK_VALUE" in stuck[-1].flags
    spike = verdicts([Decimal(2600), Decimal(9000)])
    assert spike[1].quality == "rejected" and "RATE_EXCEEDED" in spike[1].flags
    assert (
        verdicts([Decimal(2600), Decimal(2601)], step_s=0)[1].quality == "uncertain"
    )  # no time: rate unproven


@EXAMPLES
@given(values=st.lists(st.decimals(min_value=-1000, max_value=70000, allow_nan=False, places=2), max_size=20))
def test_the_guard_never_raises_and_only_returns_the_four_qualities(values: list[Decimal]) -> None:
    guard = Guard(
        Criteria(
            basis="b",
            minimum=Decimal(0),
            maximum=Decimal(50000),
            stuck_count=3,
            stuck_span_s=Decimal(1),
            max_rate_per_s=Decimal(10),
        )
    )
    at = datetime(2026, 1, 1, tzinfo=UTC)
    for i, value in enumerate(values):
        assert guard.assess(value, at + timedelta(seconds=i)).quality in {"accepted", "rejected", "uncertain"}


# ----- XTCE and CCSDS ------------------------------------------------------------------------------------------

DOSE_APID = 514  # the apid ccsds-packets.json gives the dose_rate stream, written here from that file's text


def dose_twin() -> PacketTwin:
    return PacketTwin(load_twin_world(SIM_CONFIG).packets, "dose_rate")


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(packet=st.binary(max_size=40))
def test_garbage_packets_are_rejected_with_codes_and_never_raise_other_errors(packet: bytes) -> None:
    containers = load_xtce(dose_twin().xtce_document())
    try:
        found = parse_packet(containers, packet)
    except ParseRejected as rejected:
        assert rejected.code in {
            "SHORT_PACKET",
            "NOT_TELEMETRY",
            "BAD_LENGTH",
            "UNKNOWN_APID",
            "NON_FINITE",
            "BAD_ENCODING",
        }
    else:
        assert all(p.quantity.value.is_finite() for p in found)


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(value=st.floats(width=32, allow_nan=True, allow_infinity=True), count=st.integers(0, 0x3FFF))
def test_float_packets_decode_exactly_and_non_finite_values_are_rejected(value: float, count: int) -> None:
    containers = load_xtce(dose_twin().xtce_document())
    packet = space_packet(DOSE_APID, struct.pack(">f", value), count)
    if value != value or value in (float("inf"), float("-inf")):
        with pytest.raises(ParseRejected) as caught:
            parse_packet(containers, packet)
        assert caught.value.code == "NON_FINITE"
        return
    [found] = parse_packet(containers, packet)
    assert found.quantity.value == Decimal(repr(value))
    assert found.quantity.unit == "µGy/h"


def test_xtce_documents_with_entities_or_missing_units_are_refused() -> None:
    good = dose_twin().xtce_document()
    with pytest.raises(FeedInvalid):
        load_xtce(b'<!DOCTYPE x [<!ENTITY a "b">]>' + good)
    with pytest.raises(FeedInvalid):
        load_xtce(good.replace(b"<Unit>\xc2\xb5Gy/h</Unit>", b"<Unit></Unit>"))
    with pytest.raises(FeedInvalid):
        load_xtce(b"<not xml")
    with pytest.raises(FeedInvalid):
        load_xtce(good.replace(b"mostSignificantByteFirst", b"leastSignificantByteFirst"))


@pytest.mark.trace("T-E23-074", "T-E23-062")
def test_the_packet_twin_streams_simulated_dose_and_co2_through_the_real_xtce_parser(
    world: TwinWorld,
) -> None:
    scenario = replace(world.scenarios["station_nominal"], duration_s=1800.0)
    ticks = list(world.ticks(scenario))
    for stream, device, output, unit in (
        ("dose_rate", "sim_area_dosimeter_cabin", "dose_rate", "µGy/h"),
        ("co2_ppm", "sim_ndir_co2_cabin", "co2", "ppm"),
    ):
        twin = PacketTwin(world.packets, stream)
        assert twin.simulated is True
        source = XtceSource(
            load_xtce(twin.xtce_document()), twin.packets(iter(ticks)), "ch", stream, FakeClock()
        )
        got = [i.row.quantity for i in drain(source) if i.row]
        registers = [
            r.quantity.value for t in ticks for r in t.readings if (r.device_id, r.output) == (device, output)
        ]
        assert 0 < len(got) <= len(registers)  # a device still warming up sends no packet
        assert {q.unit for q in got} == {unit}
        for quantity, register in zip(got, registers[len(registers) - len(got) :], strict=True):
            assert float(quantity.value) == pytest.approx(register, rel=1e-6, abs=1e-9)


@pytest.mark.trace("T-E23-062", "AGENTS-5")
def test_a_packet_for_another_apid_is_rejected_and_a_gap_stays_a_gap(world: TwinWorld) -> None:
    containers = load_xtce(dose_twin().xtce_document())
    other = PacketTwin(world.packets, "co2_ppm")
    scenario = replace(world.scenarios["station_nominal"], duration_s=600.0)
    packets = list(other.packets(world.ticks(scenario)))
    assert packets
    with pytest.raises(ParseRejected) as caught:
        parse_packet(containers, packets[0])
    assert caught.value.code == "UNKNOWN_APID"
    source = XtceSource(containers, iter(packets), "ch", "dose_rate", FakeClock())
    assert {i.reject_code for i in drain(source)} == {"UNKNOWN_APID"}


@pytest.mark.trace("T-E23-062")
def test_the_demo_sim_feed_posts_co2_packets_from_the_simulated_device() -> None:
    feed = feed_by_id("feed_co2_cabin_sim")
    assert feed.sim is not None and feed.sim["protocol"] == "xtce"
    source = build_sim_source(feed, FakeClock(), datetime(2026, 1, 1, tzinfo=UTC))
    first = [i for _, i in zip(range(10), source.items(0), strict=False)]
    assert all(i.row is not None and i.row.quantity.unit == "ppm" for i in first)


# ----- SIM kinds -----------------------------------------------------------------------------------------------


def sim_feed(sim: dict[str, Any]) -> Feed:
    return Feed(
        feed_id="s", kind="sim", adapter_id="adp_sim", channel_id="ch_s", unit="mmHg", pace=Pace("burst"), replay=None,
        file=None, columns=None, row_filter=None, carry_naive=False, window_from=None, window_to=None, sim=sim,
        cabin_pressure=CabinPressure(Decimal(760), "assumed"), criteria=None, basis={},
    )  # fmt: skip


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(seed=st.integers(0, 2**32), count=st.integers(1, 30), resume=st.integers(0, 35))
def test_the_seeded_generator_is_deterministic_and_resumes_by_position(
    seed: int, count: int, resume: int
) -> None:
    feed = sim_feed(
        {"type": "generator", "seed": seed, "start": 2.0, "step_sd": 0.1, "interval_s": 5, "count": count}
    )
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    whole = list(GeneratorSource(feed, epoch).items(0))
    assert [i.row for i in whole] == [i.row for i in GeneratorSource(feed, epoch).items(0)]
    tail = list(GeneratorSource(feed, epoch).items(resume))
    assert tail == [i for i in whole if i.position > resume]


def test_frozen_rows_use_offsets_from_the_run_epoch_and_reject_nothing_silently() -> None:
    feed = sim_feed(
        {"type": "frozen", "rows": [{"offset_s": 0, "value": 1.5}, {"offset_s": 5, "value": 1.25}]}
    )
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    items = list(FrozenSource(feed, epoch).items(0))
    assert [str(i.row.quantity.value) for i in items if i.row] == ["1.5", "1.25"]
    assert items[1].row is not None and items[1].row.observed_at == epoch + timedelta(seconds=5)
    assert list(FrozenSource(feed, epoch).items(1))[0].position == 2


def test_a_sim_feed_reaching_a_device_that_is_not_simulated_is_refused() -> None:
    twin_feed = feed_by_id("feed_co2_cabin_sim")
    source = build_sim_source(twin_feed, FakeClock(), datetime(2026, 1, 1, tzinfo=UTC))
    assert source is not None
    with pytest.raises(FeedInvalid):
        build_sim_source(
            sim_feed(
                {
                    "type": "twin",
                    "protocol": "ble_ess",
                    "scenario": "nope",
                    "select": "0x2A6E",
                    "config_dir": str(SIM_CONFIG),
                }
            ),
            FakeClock(),
            datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_notifications_for_other_characteristics_are_ignored_and_unknown_ones_refused() -> None:
    class Link:
        def identity(self) -> DeviceIdentity:
            return DeviceIdentity("t", "s", True)

        def notifications(self) -> Any:
            yield Notification(0x2A6F, struct.pack("<H", 1234))
            yield Notification(0x2B8C, struct.pack("<H", 2650))

        def close(self) -> None:
            return None

    items = drain(EssSource(Link(), "ch", 0x2B8C, FakeClock()))
    assert [str(i.row.quantity.value) for i in items if i.row] == ["2650"]
    with pytest.raises(ParseRejected):
        EssSource(Link(), "ch", 0x1234, FakeClock())
