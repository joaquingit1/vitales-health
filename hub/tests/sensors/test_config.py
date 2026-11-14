"""E23 section 12 tests T-E23-050, 055, 065, 070, 083, 090, 092 (configuration and loader checks)."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from e23_helpers import (
    BOUNDS,
    PRODUCT,
    ROOT,
    channel_for,
    document_view,
    evaluation_input,
    sensor,
    ucum_for,
    utc,
)

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryUnits
from crew_health.kernel.uow import UnitOfWork
from crew_health.sensors.adapters.config_reader import (
    Check4SensorLimits,
    Check12Sensors,
    ScanBoundGuard,
    SensorConfig,
    SensorConfigHolder,
    load_sensor_config,
    parse_catalog,
    parse_hazards,
    parse_limits,
)
from crew_health.sensors.domain.card import CardInput, ChannelView, compose_card
from crew_health.sensors.domain.channel import AggregationSpec
from crew_health.sensors.domain.evaluation import evaluate_limits
from crew_health.sensors.domain.loader_checks import (
    SensorBounds,
    check_catalog,
    check_limits_against_catalog,
    check_limits_against_hazards,
    check_scan_bound,
)
from crew_health.sensors.domain.reading import Instant

pytestmark = pytest.mark.e23
Docs = dict[ConfigName, bytes]
Edit = Callable[[dict[str, Any]], None]


def dumps(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def edited(raw: Docs, name: ConfigName, edit: Edit) -> Docs:
    document = json.loads(raw[name])
    edit(document)
    return {**raw, name: dumps(document)}


def candidate(raw: Docs, release_id: str = "rel_candidate") -> CandidateRelease:
    return CandidateRelease(release_id, {name: document_view(name, data) for name, data in raw.items()})


def checks(units: MemoryUnits, bounds: SensorBounds = BOUNDS) -> tuple[Check4SensorLimits, Check12Sensors]:
    return Check4SensorLimits(), Check12Sensors(bounds, units, ucum_for)


def findings(raw: Docs, units: MemoryUnits, bounds: SensorBounds = BOUNDS) -> list[str]:
    release = candidate(raw)
    return [
        f"{f.check_id}:{f.pointer}:{f.message}"
        for check in checks(units, bounds)
        for f in check.check(release)
    ]


# --- shipped documents ------------------------------------------------------------------------------------


@pytest.mark.trace("T-E23-050", "T-E23-055", "R43", "CONTRACTS11")
def test_shipped_sensor_documents_are_the_package_examples_and_pass_every_check(
    raw_documents: Docs, units: MemoryUnits
) -> None:
    for name in ("sensor-catalog", "sensor-limits"):
        shipped = json.loads((ROOT / "config" / f"{name}.json").read_text(encoding="utf-8"))
        example = json.loads(
            (PRODUCT / "api/examples/config" / f"{name}.example.json").read_text(encoding="utf-8")
        )
        assert (
            shipped == example["value"]
        )  # provenance, sources and notices exactly as the package states them
        assert shipped["notice"] == "Unreviewed draft: needs clinician review before live guidance."
    shipped_docs = {
        **raw_documents,
        ConfigName.SENSOR_CATALOG: (ROOT / "config/sensor-catalog.json").read_bytes(),
        ConfigName.SENSOR_LIMITS: (ROOT / "config/sensor-limits.json").read_bytes(),
    }
    assert findings(shipped_docs, units) == []
    config = load_sensor_config(lambda name: shipped_docs[name])
    assert len(config.sensor_types) == 8 and len(config.limit_entries) == 15
    assert config.notices == (
        "Unreviewed draft: needs clinician review before live guidance.",
    )  # kept, never dropped


@pytest.mark.trace("T-E23-050", "CONTRACTS11")
def test_pending_limits_keep_their_null_value_and_sources(config: SensorConfig) -> None:
    pending = [e.limit for e in config.limit_entries if e.limit.status == "threshold_pending"]
    assert [p.limit_id for p in pending] == ["rad_rate_watch", "rad_rate_act"]
    assert all(p.value is None and p.clear.value is None and p.source_ids == ("EV01",) for p in pending)
    assert all(
        e.value_from == "pending" for e in config.limit_entries if e.limit.status == "threshold_pending"
    )


@pytest.mark.trace("T-E23-055", "AGENTS3")
def test_catalog_numbers_are_exact_decimals_never_floats(config: SensorConfig) -> None:
    oxygen = sensor(config, "o2")
    assert oxygen.accepted_unit("kPa") is not None
    assert oxygen.accepted_unit("kPa").factor == Decimal("7.50062")  # type: ignore[union-attr]
    assert sensor(config, "dust").display.range_for("mars_surface") == (Decimal(0), Decimal("0.15"))
    assert sensor(config, "dust").display.range_for("lunar_surface") == (Decimal(0), Decimal("0.6"))


# --- T-E23-050: a configuration edit changes content with no code change ---------------------------------------


@pytest.mark.trace("T-E23-050", "R43", "V75")
def test_a_nonclinical_edit_changes_name_range_and_effect_filter_with_no_code_change(
    raw_documents: Docs, config: SensorConfig
) -> None:
    def rename(document: dict[str, Any]) -> None:
        document["sensor_types"][0]["name"] = "Carbon dioxide gas"
        document["sensor_types"][0]["display"]["range"] = [0, 6]

    def widen_filter(document: dict[str, Any]) -> None:
        document["effect_filters"][0]["only_in"] = ["mars_surface", "lunar_surface"]

    changed = edited(raw_documents, ConfigName.SENSOR_CATALOG, rename)
    changed = edited(changed, ConfigName.ENVIRONMENT_PROFILES, widen_filter)
    after = load_sensor_config(lambda name: changed[name])

    def render(cfg: SensorConfig, kind: str, env: str) -> tuple[str, tuple[Decimal, Decimal], int]:
        sensor_type = sensor(cfg, kind)
        hazard = cfg.hazard(sensor_type.hazard_id)
        assert hazard is not None
        inp = evaluation_input(
            cfg,
            kind,
            [Instant(utc(2026, 1, 1), Decimal("0.01"), Decimal("0.01"))],
            interval=60,
            environment=env,
        )
        views = (
            ChannelView(
                channel_for(cfg, kind, environments=(env,)), evaluate_limits(inp), "current", None, None
            ),
        )
        card = compose_card(
            CardInput(
                sensor_type,
                env,
                views,
                cfg.limits,
                hazard,
                cfg.effect_filters,
                (),
                "n/a",
                "REPLAY",
                "Replay",
                utc(2026, 1, 2),
            )
        )
        return card.name, card.display_range, len(card.effects)

    assert render(config, "co2", "station_leo")[:2] == ("CO2", (Decimal(0), Decimal(5)))
    assert render(after, "co2", "station_leo")[:2] == ("Carbon dioxide gas", (Decimal(0), Decimal(6)))
    assert render(after, "dust", "lunar_surface")[2] == render(config, "dust", "lunar_surface")[2] + 1


# --- T-E23-055: corrupt or missing candidates are rejected and the last good stays -----------------------------


@pytest.mark.trace("T-E23-055", "AGENTS3", "R43")
def test_a_clean_candidate_is_adopted_and_a_corrupt_one_leaves_the_last_good(
    raw_documents: Docs, units: MemoryUnits, config: SensorConfig
) -> None:
    holder = SensorConfigHolder()
    assert holder.current is None
    assert holder.adopt(candidate(raw_documents), checks(units)) == ()
    good = holder.current
    assert good is not None and good.sensor_types == config.sensor_types
    broken_cases: list[Docs] = [
        {**raw_documents, ConfigName.SENSOR_CATALOG: b"\x00\xff not json"},
        {**raw_documents, ConfigName.SENSOR_LIMITS: b'{"notice": "x", "limits": NaN}'},
        {**raw_documents, ConfigName.SENSOR_CATALOG: b'{"notice": "", "sensor_types": []}'},
        {k: v for k, v in raw_documents.items() if k != ConfigName.ENVIRONMENT_HAZARDS},
        edited(raw_documents, ConfigName.SENSOR_LIMITS, lambda d: d["limits"][0].__setitem__("value", 99)),
    ]
    for broken in broken_cases:
        assert holder.adopt(candidate(broken, "rel_bad"), checks(units)) != ()
        assert holder.current is good  # the last good release stays active


@pytest.mark.trace("T-E23-055", "CONTRACTS11")
def test_non_object_and_non_finite_documents_raise_config_invalid() -> None:
    for raw in (b"[]", b"3", b'{"notice": "n", "sensor_types": [{"x": Infinity}]}', b""):
        with pytest.raises(ProblemError) as caught:
            parse_catalog(raw)
        assert caught.value.code.value == "CONFIG_INVALID"
    with pytest.raises(ProblemError):
        parse_limits(b"{}")
    with pytest.raises(ProblemError):
        parse_hazards(b"{}")


@pytest.mark.trace("T-E23-055", "CONTRACTS11")
@pytest.mark.parametrize(
    ("document", "edit", "expect"),
    [
        (
            ConfigName.SENSOR_LIMITS,
            lambda d: d["limits"][0]["hazard_level"].__setitem__("value", 4),
            "hazard_level/value",
        ),
        (
            ConfigName.SENSOR_LIMITS,
            lambda d: d["limits"][0]["hazard_level"].__setitem__("window", "2-hour average"),
            "hazard_level/window",
        ),
        (
            ConfigName.SENSOR_LIMITS,
            lambda d: d["limits"][0]["hazard_level"].__setitem__("unit", "kPa"),
            "hazard_level/unit",
        ),
        (
            ConfigName.SENSOR_LIMITS,
            lambda d: d["limits"][0]["hazard_level"].__setitem__("level", "watch"),
            "hazard_level/level",
        ),
        (
            ConfigName.SENSOR_LIMITS,
            lambda d: d["limits"][0]["hazard_level"].__setitem__("index", 40),
            "hazard_level",
        ),
        (ConfigName.SENSOR_LIMITS, lambda d: d["limits"][0].__setitem__("value", 4), "limits/0/value"),
        (
            ConfigName.SENSOR_LIMITS,
            lambda d: d["limits"][9].__setitem__("value", 2),
            "limits/9/value",
        ),  # rule_text number
        (
            ConfigName.SENSOR_LIMITS,
            lambda d: d["limits"][14].__setitem__("value", 1),
            "limits/14/value",
        ),  # pending needs null
        (
            ConfigName.SENSOR_LIMITS,
            lambda d: d["limits"][1].__setitem__("card_mark", True),
            "more than one card_mark",
        ),
        (
            ConfigName.SENSOR_LIMITS,
            lambda d: d["limits"][0].__setitem__("aggregation_id", "mean_9d"),
            "aggregation_id",
        ),
        (ConfigName.SENSOR_LIMITS, lambda d: d["limits"][0].__setitem__("unit", "kPa"), "limits/0/unit"),
        (
            ConfigName.SENSOR_LIMITS,
            lambda d: d["limits"][0].__setitem__("sensor_type_id", "ghost"),
            "sensor_type_id",
        ),
        (
            ConfigName.SENSOR_LIMITS,
            lambda d: d["limits"][0].__setitem__("status", "threshold_pending"),
            "limits/0/status",
        ),
        (ConfigName.SENSOR_LIMITS, lambda d: d["limits"][0].__setitem__("source_ids", []), "source_ids"),
        (
            ConfigName.SENSOR_CATALOG,
            lambda d: d["sensor_types"][0].__setitem__("display_aggregation_id", "nope"),
            "display_aggregation_id",
        ),
        (
            ConfigName.SENSOR_CATALOG,
            lambda d: d["sensor_types"][0]["aggregations"][1].__setitem__("window_seconds", None),
            "window_seconds",
        ),
        (
            ConfigName.SENSOR_CATALOG,
            lambda d: d["sensor_types"][0]["aggregations"][0].__setitem__("window_seconds", 60),
            "window_seconds",
        ),
        (
            ConfigName.SENSOR_CATALOG,
            lambda d: d["sensor_types"][0]["aggregations"].append(
                dict(d["sensor_types"][0]["aggregations"][0])
            ),
            "duplicate aggregation",
        ),
        (
            ConfigName.SENSOR_CATALOG,
            lambda d: d["sensor_types"].append(dict(d["sensor_types"][0])),
            "duplicate sensor type",
        ),
        (
            ConfigName.SENSOR_CATALOG,
            lambda d: d["sensor_types"][0]["accepted_units"][1].__setitem__("conversion", "identity"),
            "identity applies only",
        ),
        (
            ConfigName.SENSOR_CATALOG,
            lambda d: d["sensor_types"][1]["accepted_units"][1].pop("factor"),
            "factor",
        ),
        (
            ConfigName.SENSOR_CATALOG,
            lambda d: d["sensor_types"][1]["accepted_units"][1].__setitem__("factor", 0),
            "factor",
        ),
        (
            ConfigName.SENSOR_CATALOG,
            lambda d: d["sensor_types"][0]["accepted_units"].pop(0),
            "canonical unit must be accepted",
        ),
        (
            ConfigName.SENSOR_CATALOG,
            lambda d: d["sensor_types"][0].__setitem__("interval_source_ids", []),
            "interval_source_ids",
        ),
        (
            ConfigName.SENSOR_CATALOG,
            lambda d: d["sensor_types"][0].__setitem__("stale_after_seconds", 4),
            "stale_after_seconds",
        ),
    ],
)
def test_each_loader_check_rejects_its_violation(
    raw_documents: Docs, units: MemoryUnits, document: ConfigName, edit: Edit, expect: str
) -> None:
    found = findings(edited(raw_documents, document, edit), units)
    assert any(expect in line for line in found), found


@pytest.mark.trace("T-E23-055", "CONTRACTS11", "S5.4")
def test_a_unit_without_a_mapping_or_with_the_wrong_dimension_rejects_the_candidate(
    raw_documents: Docs, units: MemoryUnits
) -> None:
    def no_mapping(sensor_type_id: str, unit: str) -> str | None:
        return None if unit == "ppm" else ucum_for(sensor_type_id, unit)

    config = load_sensor_config(lambda name: raw_documents[name])
    missing = check_catalog(config.sensor_types, BOUNDS, units, no_mapping)
    assert any("no sourced unit mapping" in f.message for f in missing)
    swapped = edited(
        raw_documents,
        ConfigName.SENSOR_CATALOG,
        lambda d: d["sensor_types"][3]["accepted_units"][1].__setitem__("unit", "µGy/h"),
    )
    assert any("dimension differs" in line for line in findings(swapped, units))
    unknown = MemoryUnits({"mm[Hg]": ("pressure", Decimal(1), Decimal(0))})
    assert any(
        "not a known UCUM unit" in line or "no sourced" in line for line in findings(raw_documents, unknown)
    )
    assert check_catalog(config.sensor_types, BOUNDS, units, ucum_for) == ()


# --- T-E23-065: D15 scratch threshold edit ---------------------------------------------------------------------


@pytest.mark.trace("T-E23-065", "D15", "V75", "R43")
def test_a_sourced_threshold_edited_consistently_in_a_scratch_release_changes_marks_and_evaluation(
    raw_documents: Docs, units: MemoryUnits, config: SensorConfig
) -> None:
    shipped_before = (ROOT / "config/sensor-limits.json").read_bytes()

    def hazard_edit(document: dict[str, Any]) -> None:
        co2 = next(h for h in document["hazards"] if h["id"] == "co2")
        assert co2["levels"][2]["level"] == "act"
        co2["levels"][2]["value"] = 4

    def limit_edit(document: dict[str, Any]) -> None:
        limit = document["limits"][0]
        assert limit["limit_id"] == "co2_1h_act"
        limit["value"] = 4
        limit["hazard_level"]["value"] = 4

    scratch = edited(
        edited(raw_documents, ConfigName.ENVIRONMENT_HAZARDS, hazard_edit),
        ConfigName.SENSOR_LIMITS,
        limit_edit,
    )
    assert findings(scratch, units) == []  # check 4 passes because both documents moved together
    only_limits = edited(raw_documents, ConfigName.SENSOR_LIMITS, limit_edit)
    lonely = findings(only_limits, units)
    assert any(
        "hazard_level/value" in line for line in lonely
    )  # the limit's copy no longer equals the hazard file
    assert any("limits/0/value" in line for line in lonely)  # and neither does the limit value
    after = load_sensor_config(lambda name: scratch[name])
    before_mark = {e.limit.limit_id: e.limit for e in config.limit_entries}["co2_1h_act"]
    after_mark = {e.limit.limit_id: e.limit for e in after.limit_entries}["co2_1h_act"]
    assert (before_mark.value, after_mark.value) == (Decimal(3), Decimal(4))
    assert (after_mark.level, after_mark.comparator, after_mark.role) == (
        before_mark.level,
        before_mark.comparator,
        before_mark.role,
    )

    def evaluate(cfg: SensorConfig) -> tuple[str, str | None]:
        end = utc(2014, 10, 7, 15, 36)
        rows = [
            Instant(end - timedelta(minutes=5 * n), Decimal("3.5003"), Decimal("3.5003")) for n in range(12)
        ]
        inp = evaluation_input(cfg, "co2", rows, interval=300)
        out = evaluate_limits(inp)
        return out.status, out.limit_id

    assert evaluate(config) == ("beyond_limit", "co2_1h_act")
    assert evaluate(after) == ("within_limit", None)  # the new value, with no code change
    assert (
        ROOT / "config/sensor-limits.json"
    ).read_bytes() == shipped_before  # shipped documents never edited


# --- T-E23-070: criteria missing --------------------------------------------------------------------------------


@pytest.mark.trace("T-E23-070", "DE23-8", "R-E23-2")
def test_the_catalog_schema_carries_no_plausibility_criteria_so_none_are_invented(
    config: SensorConfig,
) -> None:
    for kind in config.sensor_types:
        assert not hasattr(kind, "plausibility") and not hasattr(kind, "range_check")


# --- T-E23-083, 092: guards and bounds ---------------------------------------------------------------------------


def with_peak(raw: Docs, window: int, interval: int | None) -> Docs:
    def catalog(document: dict[str, Any]) -> None:
        noise = next(s for s in document["sensor_types"] if s["id"] == "noise")
        noise["aggregations"].append({"id": "peak", "kind": "max", "window_seconds": window, "label": "peak"})
        noise["expected_interval_seconds"] = interval

    def limits(document: dict[str, Any]) -> None:
        extra = copy.deepcopy(
            next(item for item in document["limits"] if item["limit_id"] == "noise_peak_emergency")
        )
        extra["limit_id"] = "noise_hour_peak"
        extra["aggregation_id"] = "peak"
        document["limits"].append(extra)

    return edited(edited(raw, ConfigName.SENSOR_CATALOG, catalog), ConfigName.SENSOR_LIMITS, limits)


def guard(overrides: dict[str, int], scan_max: int = 20000) -> ScanBoundGuard:
    return ScanBoundGuard(scan_max, lambda uow: overrides)


@pytest.mark.trace("T-E23-083", "T-E23-092", "3.7", "8.1")
def test_the_activation_guard_rejects_a_max_window_that_could_exceed_the_scan_bound(
    raw_documents: Docs,
) -> None:
    nobody = cast(UnitOfWork, object())
    ok = candidate(with_peak(raw_documents, 20000, 1))  # 20000 readings at one per second: exactly the bound
    assert guard({}).check(nobody, ok) == ()
    over = candidate(with_peak(raw_documents, 20001, 1))
    assert [f.pointer for f in guard({}).check(nobody, over)] == ["/limits/15/aggregation_id"]
    slower = candidate(with_peak(raw_documents, 3600, 5))
    assert guard({}).check(nobody, slower) == ()
    assert guard({"noise": 1}, 3599).check(nobody, slower) != ()  # a registry override at 1 s reads 3600 rows
    assert guard({"noise": 5}, 3599).check(nobody, slower) == ()
    unbounded = candidate(with_peak(raw_documents, 3600, None))
    assert guard({}).check(nobody, unbounded) != ()  # no interval, no proof of a bound
    assert guard({"noise": 60}).check(nobody, unbounded) == ()  # a registry interval gives the bound
    plain = candidate(raw_documents)
    assert guard({}).check(nobody, plain) == ()  # mean windows use buckets, not scans
    corrupt = candidate({**raw_documents, ConfigName.SENSOR_CATALOG: b"{"})
    assert guard({}).check(nobody, corrupt) != ()


@pytest.mark.trace("T-E23-092", "3.7", "S5.11")
def test_window_bucket_and_edge_bounds_at_the_limit_and_over_it(
    raw_documents: Docs, units: MemoryUnits
) -> None:
    config = load_sensor_config(lambda name: raw_documents[name])
    seven_days = 604800  # the catalog's longest window
    exact = SensorBounds(60, seven_days // 60, 20000)
    assert check_catalog(config.sensor_types, exact, units, ucum_for) == ()
    over = SensorBounds(60, seven_days // 60 - 1, 20000)
    assert any("buckets" in f.message for f in check_catalog(config.sensor_types, over, units, ucum_for))
    coarse = SensorBounds(3600, 168, 20000)
    assert check_catalog(config.sensor_types, coarse, units, ucum_for) == ()
    # an edge bucket holds ceil(60 s / 5 s) = 12 readings at the CO2 cadence
    assert check_catalog(config.sensor_types, SensorBounds(60, 10080, 12), units, ucum_for) == ()
    assert any(
        "edge bucket" in f.message
        for f in check_catalog(config.sensor_types, SensorBounds(60, 10080, 11), units, ucum_for)
    )


@pytest.mark.trace("T-E23-092", "3.7")
def test_scan_bound_rule_without_catalog_documents(config: SensorConfig) -> None:
    by_id = {s.id: s for s in config.sensor_types}
    peak = replace(
        by_id["noise"], aggregations=(*by_id["noise"].aggregations, AggregationSpec("peak", "sum", 100, "x"))
    )
    entry = replace(
        next(e for e in config.limit_entries if e.limit.limit_id == "noise_peak_emergency"),
        limit=replace(
            next(e.limit for e in config.limit_entries if e.limit.limit_id == "noise_peak_emergency"),
            aggregation_id="peak",
        ),
    )
    table = {**by_id, "noise": peak}
    assert check_scan_bound(table, (entry,), lambda _id: 1, 100) == ()
    assert len(check_scan_bound(table, (entry,), lambda _id: 1, 99)) == 1
    assert len(check_scan_bound(table, (entry,), lambda _id: 0, 99)) == 1
    assert check_limits_against_catalog((entry,), table) == ()
    assert check_limits_against_hazards((), {}) == ()


@pytest.mark.trace("T-E23-072", "SYSTEM6.4", "AGENTS3")
def test_the_sensor_domain_imports_only_its_own_domain_the_pure_kernel_and_the_standard_library() -> None:
    from crew_health.app.import_contracts import check_features

    check_features(ROOT / "hub/src/crew_health", ("sensors",))  # raises ValueError on any edge outside S6.4
