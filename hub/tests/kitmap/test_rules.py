"""Domain rules (CONTRACTS 21): precedence, missing sets, regions, the kit gate and capability derivation."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crew_health.kitmap import module as kitmap_module
from crew_health.kitmap.application.errors import KitGateInvalid, KitMapUnavailable
from crew_health.kitmap.application.ports import KitGate, KitMapQueries
from crew_health.kitmap.domain.document import DocumentBounds, parse_kit_map
from crew_health.kitmap.domain.model import (
    STATE_ORDER,
    ConditionState,
    KitMapDoc,
    UnknownCapabilityCode,
    UnknownConditionId,
)
from crew_health.kitmap.domain.rules import (
    count_states,
    derive_capabilities,
    diff_states,
    evaluate_kit,
    kit_gate,
    summarize_regions,
)

from .conftest import CONFIG, EXAMPLE, SETTINGS, FakeMission, Hub, release, ui_copy_raw

pytestmark = [pytest.mark.trace("R44", "V76", "V78", "KM-I1", "KM-I2", "KM-I3", "KM-I4", "KM-I5", "KM-I6")]

WORDS = kitmap_module.vocabulary()
BOUNDS = DocumentBounds(
    **{key: cast(int, SETTINGS["kitmap." + key]) for key in DocumentBounds.__dataclass_fields__}
)
AUTHORED = parse_kit_map(CONFIG.read_bytes(), BOUNDS, WORDS.environments, None)
PACKAGE_EXAMPLE = parse_kit_map(EXAMPLE.read_bytes(), BOUNDS, WORDS.environments, None)
RANK = {state: rank for rank, state in enumerate(STATE_ORDER)}


def instruments(doc: KitMapDoc) -> list[str]:
    return [i.id for i in doc.instruments]


kits = st.sets(st.sampled_from(instruments(AUTHORED))).map(frozenset)


def test_golden_counts_of_the_package_example() -> None:
    """CONTRACTS 21 example: station 0, 3, 4; Mars 1, 3, 3 (independently stated in the package examples)."""
    station = count_states(evaluate_kit(PACKAGE_EXAMPLE, PACKAGE_EXAMPLE.kits["station_leo"]))
    mars = count_states(evaluate_kit(PACKAGE_EXAMPLE, PACKAGE_EXAMPLE.kits["mars_surface"]))
    assert (station.measured_here, station.report_only, station.needs_test_not_aboard) == (0, 3, 4)
    assert (mars.measured_here, mars.report_only, mars.needs_test_not_aboard) == (1, 3, 3)


def test_q26_golden_rows_of_the_authored_map() -> None:
    """Coordinator decision Q26, read independently of the build: ICL100 SANS is one joint test (every instrument
    needed), ICL8 has one examination test with no instrument, ICL84 SIRS is one joint test."""
    rows = {r.condition_id: r for r in AUTHORED.conditions}
    assert len(rows["ICL100"].tests) == 1 and len(rows["ICL100"].tests[0].instruments) > 1
    assert [t.instruments for t in rows["ICL8"].tests] == [()]
    assert len(rows["ICL84"].tests) == 1 and len(rows["ICL84"].tests[0].instruments) > 1
    # The Mars kit carries the ultrasound but not a fundoscope: a joint SANS test is still not aboard there.
    mars = {e.row.condition_id: e for e in evaluate_kit(AUTHORED, AUTHORED.kits["mars_surface"])}
    assert mars["ICL100"].state is ConditionState.NEEDS_TEST_NOT_ABOARD
    assert mars["ICL8"].state is ConditionState.REPORT_ONLY


def test_partial_tests_never_count() -> None:
    """E27 3.2: ICL3 with the ECG but no troponin stays needs_test_not_aboard (package example row)."""
    kit = PACKAGE_EXAMPLE.kits["station_leo"] | {"ecg_12_lead"}
    icl3 = next(e for e in evaluate_kit(PACKAGE_EXAMPLE, kit) if e.row.condition_id == "ICL3")
    assert icl3.state is ConditionState.NEEDS_TEST_NOT_ABOARD
    assert icl3.missing_instruments == ("troponin_poc",)


@settings(max_examples=200, deadline=None)
@given(kit=kits)
def test_every_row_has_one_state_and_counts_add_up(kit: frozenset[str]) -> None:
    evaluations = evaluate_kit(AUTHORED, kit)
    counts = count_states(evaluations)
    assert counts.total == len(AUTHORED.conditions) == len(evaluations)
    assert counts.measured_here + counts.report_only + counts.needs_test_not_aboard == counts.total
    for e in evaluations:
        measurable = any(t.instruments and set(t.instruments) <= kit for t in e.row.tests)
        assert (e.state is ConditionState.MEASURED_HERE) == measurable
        if e.state is ConditionState.MEASURED_HERE:
            assert e.missing_instruments == ()


@settings(max_examples=200, deadline=None)
@given(kit=kits)
def test_missing_set_is_minimal_with_ties_to_the_first_test(kit: frozenset[str]) -> None:
    for e in evaluate_kit(AUTHORED, kit):
        if e.state is not ConditionState.NEEDS_TEST_NOT_ABOARD:
            continue
        sizes = [len([i for i in t.instruments if i not in kit]) for t in e.row.tests]
        first = sizes.index(min(sizes))
        assert e.missing_instruments == tuple(i for i in e.row.tests[first].instruments if i not in kit)


@settings(max_examples=200, deadline=None)
@given(kit=kits, extra=kits)
def test_adding_instruments_never_moves_a_row_down(kit: frozenset[str], extra: frozenset[str]) -> None:
    before = evaluate_kit(AUTHORED, kit)
    after = evaluate_kit(AUTHORED, kit | extra)
    for b, a in zip(before, after, strict=True):
        assert RANK[a.state] <= RANK[b.state]
    moved = diff_states(before, after)
    assert all(b.state != a.state for b, a in moved)
    assert len(moved) == sum(1 for b, a in zip(before, after, strict=True) if b.state != a.state)


@settings(max_examples=100, deadline=None)
@given(kit=kits)
def test_region_marks_are_ordered_and_exact(kit: frozenset[str]) -> None:
    evaluations = evaluate_kit(AUTHORED, kit)
    regions = summarize_regions(evaluations, WORDS.body_regions)
    order = [*WORDS.body_regions, "whole_body"]
    assert [r.region for r in regions] == sorted((r.region for r in regions), key=order.index)
    for summary in regions:
        members = [
            e
            for e in evaluations
            if (summary.region in e.row.body_regions)
            or (summary.region == "whole_body" and not e.row.body_regions)
        ]
        assert members and summary.counts == count_states(members)
        assert list(summary.marks) == [s for s in STATE_ORDER if any(e.state is s for e in members)]


def test_gate_cannot_names_the_missing_instrument() -> None:
    result = kit_gate(AUTHORED, "ecg_rhythm", "lunar_surface", ["ICL10"])
    assert not result.can
    assert result.missing_instrument_ids == ("ecg_single_lead",)
    assert result.instrument_words == ("an ECG",)
    assert result.cliff_urls == (next(r.cliff_url for r in AUTHORED.conditions if r.condition_id == "ICL10"),)


def test_gate_can_names_the_providers_aboard() -> None:
    result = kit_gate(AUTHORED, "pulse", "station_leo", ["ICL10", "ICL10"])
    assert result.can and result.instrument_ids == ("camera_station", "pulse_oximeter")
    assert result.condition_ids == ("ICL10",)


def test_gate_rejects_unknown_codes_and_conditions() -> None:
    with pytest.raises(UnknownCapabilityCode):
        kit_gate(AUTHORED, "telepathy", "station_leo", [])
    with pytest.raises(UnknownConditionId) as raised:
        kit_gate(AUTHORED, "pulse", "station_leo", ["ICL10", "ICL999"])
    assert raised.value.index == 1


def test_capabilities_follow_the_kit() -> None:
    station = {c.code: c for c in derive_capabilities(AUTHORED, "station_leo", WORDS.environments)}
    mars = {c.code: c for c in derive_capabilities(AUTHORED, "mars_surface", WORDS.environments)}
    assert (
        station["ecg_rhythm"].kit_status == "unavailable" and station["ecg_rhythm"].kit_reason == "not_in_kit"
    )
    assert station["pulse"].kit_status == "available"
    assert station["ultrasound_imaging"].kit_status == "unavailable"
    assert mars["ultrasound_imaging"].kit_status == "available"
    assert mars["ultrasound_imaging"].applicable_environments == ("mars_surface",)


def test_kit_gate_port_renders_the_contract_text(make_hub: Callable[..., Hub], config_raw: bytes) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy_raw()), mission=FakeMission("lunar_surface"))
    gate = hub.resolver.require(KitGate)  # type: ignore[type-abstract]
    before = hub.writes()
    outcome = gate.check(
        hub.as_uow(), capability_code="ecg_rhythm", environment_id="lunar_surface", condition_ids=["ICL10"]
    )
    assert outcome.kit_check.text == "This kit can't: needs an ECG"
    assert outcome.kit_check.kit_map_version == AUTHORED.version
    can = gate.check(hub.as_uow(), capability_code="pulse", environment_id="lunar_surface", condition_ids=[])
    assert can.kit_check.text == "This kit can: camera station, pulse oximeter"
    queries = hub.resolver.require(KitMapQueries)  # type: ignore[type-abstract]
    assert "ecg_single_lead" not in queries.kit_instruments(hub.as_uow(), "lunar_surface")
    assert hub.writes() == before


def test_kit_gate_port_bounds_and_missing_copy(make_hub: Callable[..., Hub], config_raw: bytes) -> None:
    hub = make_hub(release("rel_1", config_raw, ui_copy=ui_copy_raw()), mission=FakeMission("lunar_surface"))
    gate = hub.resolver.require(KitGate)  # type: ignore[type-abstract]
    too_many = ["ICL10"] * (cast(int, SETTINGS["kitmap.gate_max_condition_ids"]) + 1)
    with pytest.raises(KitGateInvalid):
        gate.check(
            hub.as_uow(), capability_code="pulse", environment_id="lunar_surface", condition_ids=too_many
        )
    with pytest.raises(KitGateInvalid) as raised:
        gate.check(
            hub.as_uow(), capability_code="telepathy", environment_id="lunar_surface", condition_ids=[]
        )
    assert raised.value.field_errors[0].field == "/capability_code"
    no_copy = make_hub(release("rel_1", config_raw), mission=FakeMission("lunar_surface"))
    gate = no_copy.resolver.require(KitGate)  # type: ignore[type-abstract]
    with pytest.raises(KitMapUnavailable):
        gate.check(
            no_copy.as_uow(), capability_code="pulse", environment_id="lunar_surface", condition_ids=[]
        )
