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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from support import DEMO, NASA, FakeClock, feed_by_id, scratch_feeds

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
from crew_health_adapter_habitat.sim import FrozenSource, GeneratorSource, TwinBleLink, build_sim_source
from crew_health_adapter_habitat.xtce import XtceSource, load_xtce, parse_packet
from crew_health_sim_habitat import EssTwin, LinkLost, PacketTwin, space_packet

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
        assert rejected.code in {"BAD_LENGTH", "NOT_KNOWN", "SATURATED"}
        return
    assert len(payload) == 2 and value.is_finite()
    raw = int.from_bytes(payload, "little", signed=spec.fmt == "sint16")
    assert value == Decimal(raw) * spec.resolution
    assert (raw & 0xFFFF) not in {spec.unknown_raw, spec.saturated_raw}


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(raw=st.integers(0, 0xFFFD), uuid=st.sampled_from([0x2A6F, 0x2B8C]))
def test_the_twin_encoding_and_the_adapter_parser_agree_for_unsigned_characteristics(
    raw: int, uuid: int
) -> None:
    payload = struct.pack("<H", raw)  # written here, independently of both the twin and the parser
    assert decode(ESS_CHARACTERISTICS[uuid], payload) == Decimal(raw) * ESS_CHARACTERISTICS[uuid].resolution


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(raw=st.integers(-32767, 32767))
def test_temperature_round_trips(raw: int) -> None:
    assert decode(ESS_CHARACTERISTICS[0x2A6E], struct.pack("<h", raw)) == Decimal(raw) * Decimal("0.01")


def test_unknown_and_saturated_codes_are_rejected_not_turned_into_numbers() -> None:
    co2, temp = ESS_CHARACTERISTICS[0x2B8C], ESS_CHARACTERISTICS[0x2A6E]
    for payload, code in ((struct.pack("<H", 0xFFFF), "NOT_KNOWN"), (struct.pack("<H", 0xFFFE), "SATURATED")):
        with pytest.raises(ParseRejected) as caught:
            decode(co2, payload)
        assert caught.value.code == code
    with pytest.raises(ParseRejected) as caught:
        decode(temp, struct.pack("<h", -32768))
    assert caught.value.code == "NOT_KNOWN"


def test_origin_comes_from_the_device_identity_and_live_needs_sourced_criteria() -> None:
    sim = DeviceIdentity("twin", "SIM-1", simulated=True)
    real = DeviceIdentity("sensor", "AA:BB", simulated=False)
    assert origin_of(sim, None) == "SIM"
    with pytest.raises(LiveRefused):
        origin_of(real, None)
    assert origin_of(real, Criteria(basis="a sourced basis")) == "LIVE"


def drain(source: Any) -> list[SourceItem]:
    return list(source.items(0))


@pytest.mark.trace("T-E23-074", "T-E23-062")
def test_the_twin_streams_the_recorded_rr1_values_through_the_real_ess_parser(twin_data: Path) -> None:
    clock = FakeClock()
    expected = independent_rows(twin_data / "eda_rr1_telemetry.csv", column="co2_iss")
    link = TwinBleLink(EssTwin(0x2B8C, "nominal", twin_data, seed=1))
    assert link.identity().simulated is True  # declares itself simulated, so the hub labels it SIM
    items = drain(EssSource(link, "ch_co2_cabin_sim", 0x2B8C, clock))
    assert [str(i.row.quantity.value) for i in items if i.row] == [v for _, v in expected]
    assert all(i.row and i.row.quantity.unit == "ppm" for i in items)
    temps = drain(EssSource(TwinBleLink(EssTwin(0x2A6E, "nominal", twin_data, 1)), "c", 0x2A6E, clock))
    assert [Decimal(str(i.row.quantity.value)) for i in temps if i.row][:3] == [
        Decimal(v)
        for _, v in independent_rows(twin_data / "eda_rr1_telemetry.csv", column="temperature_iss")[:3]
    ]


def fault(twin_data: Path, name: str, uuid: int = 0x2B8C) -> list[SourceItem]:
    return drain(EssSource(TwinBleLink(EssTwin(uuid, name, twin_data, 3)), "ch", uuid, FakeClock()))


@pytest.mark.trace("T-E23-062", "AGENTS-4")
def test_twin_fault_scenarios_reach_the_parser_and_the_guard(twin_data: Path) -> None:
    base = fault(twin_data, "nominal")

    def codes(items: list[SourceItem]) -> list[str | None]:
        return [i.reject_code for i in items]

    assert (
        codes(fault(twin_data, "saturation"))[3:6] == ["SATURATED"] * 3
        and codes(fault(twin_data, "saturation"))[:3] == [None] * 3
    )
    assert set(codes(fault(twin_data, "not_known"))[3:]) == {"NOT_KNOWN"}
    low = fault(twin_data, "low_signal")
    assert 0 < len(low) < len(base) and [i.position for i in low] == list(
        range(1, len(low) + 1)
    )  # gaps stay gaps
    stuck = [i.row.quantity.value for i in fault(twin_data, "stuck") if i.row]
    assert len(set(stuck[2:])) == 1
    with pytest.raises(SourceUnavailable):
        drain(EssSource(TwinBleLink(EssTwin(0x2B8C, "disconnect", twin_data, 3)), "ch", 0x2B8C, FakeClock()))
    drift = [i.row.quantity.value for i in fault(twin_data, "drift") if i.row]
    assert drift[10] - drift[0] > base[10].row.quantity.value - base[0].row.quantity.value  # type: ignore[union-attr]
    noisy = [i.row.quantity.value for i in fault(twin_data, "motion_noise") if i.row]
    assert noisy[6] != base[6].row.quantity.value  # type: ignore[union-attr]
    out = [i.row.quantity.value for i in fault(twin_data, "out_of_range") if i.row]
    assert out[3] == Decimal(60000)


@pytest.mark.trace("T-E23-062", "AGENTS-2")
def test_guard_flags_each_fault_with_scratch_criteria_and_never_invents_thresholds(twin_data: Path) -> None:
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


@pytest.mark.trace("T-E23-074")
@EXAMPLES
@given(packet=st.binary(max_size=40))
def test_garbage_packets_are_rejected_with_codes_and_never_raise_other_errors(packet: bytes) -> None:
    containers = load_xtce(PacketTwin("dose_rate", "nominal", DEMO).xtce_document())
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
    containers = load_xtce(PacketTwin("dose_rate", "nominal", DEMO).xtce_document())
    packet = space_packet(513, struct.pack(">f", value), count)
    if value != value or value in (float("inf"), float("-inf")):
        with pytest.raises(ParseRejected) as caught:
            parse_packet(containers, packet)
        assert caught.value.code == "NON_FINITE"
        return
    [found] = parse_packet(containers, packet)
    assert found.quantity.value == Decimal(repr(value)) and found.quantity.unit == "µGy/h"


def test_xtce_documents_with_entities_or_missing_units_are_refused() -> None:
    good = PacketTwin("dose_rate", "nominal", DEMO).xtce_document()
    with pytest.raises(FeedInvalid):
        load_xtce(b'<!DOCTYPE x [<!ENTITY a "b">]>' + good)
    with pytest.raises(FeedInvalid):
        load_xtce(good.replace(b"<Unit>\xc2\xb5Gy/h</Unit>", b"<Unit></Unit>"))
    with pytest.raises(FeedInvalid):
        load_xtce(b"<not xml")
    with pytest.raises(FeedInvalid):
        load_xtce(good.replace(b"mostSignificantByteFirst", b"leastSignificantByteFirst"))


@pytest.mark.trace("T-E23-074", "T-E23-062")
def test_the_packet_twin_streams_recorded_radiation_and_noise_through_the_real_xtce_parser() -> None:
    twin = PacketTwin("dose_rate", "nominal", DEMO)
    assert twin.identity().simulated is True
    source = XtceSource(load_xtce(twin.xtce_document()), twin.packets(), "ch_rad", "dose_rate", FakeClock())
    items = drain(source)
    recorded = [
        float(v)
        for _, v in independent_rows(
            DEMO / "radlab_dostel_sample.csv", column="absorbed_dose_rate", where=("instrument_id", "DosTel1")
        )
    ]
    got = [float(i.row.quantity.value) for i in items if i.row]
    assert len(got) == len(recorded) and all(
        abs(g - r) <= abs(r) * 1e-6 for g, r in zip(got, recorded, strict=True)
    )
    assert all(i.row.quantity.unit == "µGy/h" for i in items if i.row)


@pytest.mark.trace("T-E23-062")
def test_the_packet_twin_faults_and_the_noise_level_come_from_the_nasa_files(twin_data: Path) -> None:
    noise = PacketTwin("sound_level", "nominal", twin_data)
    got = drain(
        XtceSource(load_xtce(noise.xtce_document()), noise.packets(), "ch_noise", "sound_level", FakeClock())
    )
    [spot] = [
        r
        for r in csv.DictReader((twin_data / "iss_acoustic_levels.csv").open(encoding="utf-8"))
        if r["location"] == "U.S. Lab"
    ]
    assert {i.row.quantity.value for i in got if i.row} == {Decimal(spot["sound_level_dba"])} and got[
        0
    ].row.quantity.unit == "dBA"  # type: ignore[union-attr]
    stuck = PacketTwin("dose_rate", "stuck", twin_data)
    values = [
        i.row.quantity.value
        for i in drain(
            XtceSource(load_xtce(stuck.xtce_document()), stuck.packets(), "c", "dose_rate", FakeClock())
        )
        if i.row
    ]
    assert len(set(values[3:])) == 1
    gone = PacketTwin("dose_rate", "disconnect", twin_data)
    with pytest.raises(LinkLost):
        list(gone.packets())


# ----- NASA case scenarios of the twin -------------------------------------------------------------------------


@pytest.mark.trace("T-E23-062", "AGENTS-4")
def test_the_co2_rise_is_shaped_from_the_printed_levels_of_the_iss_symptom_case(twin_data: Path) -> None:
    """Levels from co2_symptom_reports.csv lines 2 (2.7), 4 (above 4, relief about 2) and 3 (up to 7.5 mm Hg) at an
    assumed 760 mm Hg. The case is qualitative: the shape is not a recorded series."""
    lines = (
        (twin_data / "iss-co2-symptom-reports-2001-2009" / "co2_symptom_reports.csv")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert (
        lines[1].startswith("sts112_iss9a,")
        and lines[2].startswith("sts113_iss11a,")
        and lines[3].startswith("expedition6,")
    )

    def ppm(mmhg: float) -> Decimal:
        return Decimal(str(mmhg)) / 760 * 1_000_000

    values = [i.row.quantity.value for i in fault(twin_data, "co2_rise_iss_2001_2009") if i.row]
    assert (
        abs(values[0] - ppm(2.7)) <= 1 and abs(max(values) - ppm(7.5)) <= 1 and abs(values[-1] - ppm(2)) <= 1
    )
    assert any(abs(v - ppm(4)) <= 1 for v in values)  # passes through the Expedition 6 level
    peak = values.index(max(values))
    assert values[peak - 1] < values[peak] and values[:peak] == sorted(
        values[:peak]
    )  # rises monotonically to the peak


@pytest.mark.trace("T-E23-062", "AGENTS-4")
def test_the_solar_particle_event_replays_recorded_dostel2_rows_around_the_documented_onset(
    twin_data: Path,
) -> None:
    """spe-2017-09-iss-columbus outcome.json lines 20 and 62: onset 2017-09-10 16:06 UTC, first protons at the ISS
    about 2017-09-11 04:30 UTC. The twin replays the DosTel2 rows of 10 Sep 16:00 to 12 Sep 00:00."""
    twin = PacketTwin("dose_rate", "spe_2017_09", twin_data)
    items = drain(XtceSource(load_xtce(twin.xtce_document()), twin.packets(), "c", "dose_rate", FakeClock()))
    rows = [
        r
        for r in csv.DictReader((twin_data / "radlab_iss_2017-09_spe.csv").open(encoding="utf-8"))
        if r["instrument_id"] == "DosTel2"
        and "2017-09-10T16:00:00" <= r["timestamp"] <= "2017-09-12T00:00:00"
    ]
    got = [float(i.row.quantity.value) for i in items if i.row]
    assert len(got) == len(rows) > 1000
    assert all(
        abs(g - float(r["absorbed_dose_rate"])) <= abs(float(r["absorbed_dose_rate"])) * 1e-6
        for g, r in zip(got, rows, strict=True)
    )
    before = max(
        float(r["absorbed_dose_rate"])
        for r in rows
        if r["timestamp"] < "2017-09-11T04:00:00" and abs(float(r["latitude"])) >= 45
    )
    after = max(
        float(r["absorbed_dose_rate"])
        for r in rows
        if r["timestamp"] >= "2017-09-11T04:18:00" and abs(float(r["latitude"])) >= 45
    )
    assert after > before  # the documented rise at high latitude (outcome.json line 77)


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
                    "select": "0x2B8C",
                    "seed": 1,
                    "data_dir": str(DEMO),
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
