"""Identity corrections (E22 3.5) and the award ledger (DE22-8, CONTRACTS 8 patches; T-E22-024, 025, 035, 038, 060)."""

from __future__ import annotations

import pytest

from crew_health.participation.domain.corrections import plan_correction
from crew_health.participation.domain.derivation import (
    AwardEvent,
    active_awards,
    award_views,
    next_award_events,
    reached_attributions,
)
from crew_health.participation.domain.errors import InvalidBasis, InvalidCorrection
from crew_health.participation.domain.identity import award_id
from crew_health.participation.domain.projection import build_completion, decide
from crew_health.participation.domain.rules import parse_rules
from crew_health.participation.domain.types import CompletionRecord

from .harness import at_day, basis, clock, config_document, facts_for, rules

pytestmark = pytest.mark.E22


def original(when_day: int = 3, key: str = "k0") -> CompletionRecord:
    b = basis(ref="dck_x", when=at_day(when_day, 5), offset="-03:00")
    rec = build_completion(
        b, decide(b, record_origin="SIM"), key=key, clock=clock(), rule_version="participation-1"
    )
    assert rec is not None
    return rec


def head_of(rec: CompletionRecord, *, to: str, generation: int, key: str) -> CompletionRecord:
    plan = plan_correction(
        rec,
        to_patient_id=to,
        correction_ref=f"r{generation}",
        expected_generation=generation,
        seen_correction_refs=(),
    )
    assert plan.replacement_basis is not None and plan.replacement_key is not None
    out = build_completion(
        plan.replacement_basis,
        decide(plan.replacement_basis, record_origin="SIM", replacement=True),
        key=plan.replacement_key, clock=clock(), rule_version="participation-1",
        lineage_id=plan.lineage_id, generation=plan.next_generation or 0,
        replaces_completion_id=plan.replaces_completion_id,
    )  # fmt: skip
    assert out is not None
    return out


@pytest.mark.trace("T-E22-024", "3.5")
def test_correction_a_to_b_to_a_gives_three_rows_with_distinct_keys_and_preserved_time() -> None:
    a0 = original()
    b1 = head_of(a0, to="tomas", generation=0, key="x")
    a2 = head_of(b1, to="mara", generation=1, key="y")
    rows = [a0, b1, a2]
    assert [r.generation for r in rows] == [0, 1, 2]
    assert len({r.intent_key for r in rows}) == 3 and len({r.completion_id for r in rows}) == 3
    assert {r.lineage_id for r in rows} == {a0.completion_id}
    assert [r.basis.patient_id for r in rows] == ["mara", "tomas", "mara"]
    assert [r.replaces_completion_id for r in rows] == [None, a0.completion_id, b1.completion_id]
    for r in rows:
        assert r.basis.occurred_at == a0.basis.occurred_at
        assert r.basis.original_offset == "-03:00"
        assert (r.basis.basis, r.basis.basis_ref, r.mission_day) == ("daily_check", "dck_x", a0.mission_day)
        assert r.status == "accepted"


@pytest.mark.trace("T-E22-025", "3.5")
def test_stale_generation_is_a_conflict_and_the_same_reference_is_a_duplicate() -> None:
    a0 = original()
    applied = plan_correction(
        a0, to_patient_id="tomas", correction_ref="c1", expected_generation=0, seen_correction_refs=()
    )
    assert applied.outcome == "apply" and applied.void_completion_id == a0.completion_id
    b1 = head_of(a0, to="tomas", generation=0, key="x")
    # a second correction with the same expected generation after the first applied: the head is now generation 1
    stale = plan_correction(
        b1, to_patient_id="mara", correction_ref="c2", expected_generation=0, seen_correction_refs=("c1",)
    )
    assert (
        stale.outcome == "conflict" and stale.replacement_basis is None and stale.void_completion_id is None
    )
    again = plan_correction(
        b1, to_patient_id="mara", correction_ref="c1", expected_generation=0, seen_correction_refs=("c1",)
    )
    assert again.outcome == "duplicate"
    ahead = plan_correction(
        a0, to_patient_id="tomas", correction_ref="c3", expected_generation=5, seen_correction_refs=()
    )
    assert ahead.outcome == "conflict"
    assert len({applied.correction_key, stale.correction_key, ahead.correction_key}) == 3


@pytest.mark.trace("T-E22-025", "3.5")
def test_invalid_corrections_are_refused() -> None:
    a0 = original()
    with pytest.raises(InvalidCorrection):
        plan_correction(
            a0, to_patient_id="mara", correction_ref="c", expected_generation=0, seen_correction_refs=()
        )
    with pytest.raises(InvalidCorrection):
        plan_correction(
            a0, to_patient_id="tomas", correction_ref="c", expected_generation=-1, seen_correction_refs=()
        )
    rejected = build_completion(
        basis(actor="tomas"),
        decide(basis(actor="tomas"), record_origin="SIM"),
        key="r",
        clock=clock(),
        rule_version="v",
    )
    assert rejected is not None
    with pytest.raises(InvalidCorrection):
        plan_correction(
            rejected,
            to_patient_id="tomas",
            correction_ref="c",
            expected_generation=0,
            seen_correction_refs=(),
        )


@pytest.mark.trace("T-E22-024", "3.4")
def test_a_replacement_still_fails_the_origin_rule_so_the_void_stays_in_force() -> None:
    a0 = original()
    plan = plan_correction(
        a0, to_patient_id="tomas", correction_ref="c", expected_generation=0, seen_correction_refs=()
    )
    assert plan.replacement_basis is not None
    d = decide(plan.replacement_basis, record_origin="LIVE", replacement=True)
    assert (d.kind, d.reason) == ("rejected", "ORIGIN_MISMATCH")


def ledger_after(
    days: list[int], ledger: list[AwardEvent], *, void: bool = False, rule_set=None
) -> list[AwardEvent]:  # type: ignore[no-untyped-def]
    r = rule_set or rules()
    facts = facts_for(days)
    first, today = 1, max(days, default=1)
    total = len({f.mission_day for f in facts})
    new = next_award_events(
        ledger,
        total=total,
        reached=reached_attributions(facts, first, today, r.patch_thresholds),
        rules=r,
        void_applied=void,
    )
    return [*ledger, *new]


@pytest.mark.trace("T-E22-003", "T-E22-035", "D03")
def test_awards_are_earned_at_the_thresholds_and_not_before() -> None:
    led: list[AwardEvent] = []
    led = ledger_after(list(range(1, 7)), led)
    assert led == []
    led = ledger_after(list(range(1, 8)), led)
    assert [e.threshold for e in led] == [7]
    led = ledger_after(list(range(1, 19)), led)
    assert [e.threshold for e in led] == [7, 14]
    led_again = ledger_after(list(range(1, 19)), led)
    assert led_again == led  # nothing earned twice
    views = award_views("mara", "demo-mission", "SIM", led)
    assert [(a.threshold, a.patch_design_id) for a in views] == [(7, "patch_week"), (14, "patch_fortnight")]
    assert [a.reached_on_mission_day for a in views] == [7, 14]
    assert views[0].award_id == award_id("mara", "demo-mission", "SIM", 7)
    assert all(a.derivation_version == "participation-1" and a.origin_mode == "SIM" for a in views)


@pytest.mark.trace("T-E22-035", "INV-E22-10")
def test_only_a_void_that_drops_the_total_removes_an_award_and_it_can_be_earned_again() -> None:
    led = ledger_after(list(range(1, 9)), [])
    assert [e.threshold for e in led] == [7]
    # the total drops (a clock merge) with no void: the award is kept
    assert ledger_after([1, 2, 3, 4, 5], led) == led
    # a void that leaves the total above the threshold keeps it
    assert ledger_after(list(range(1, 9)), led, void=True) == led
    # a void that drops the total below the threshold removes it, with the recorded design
    removed = ledger_after([1, 2, 3, 4, 5, 6], led, void=True)
    assert [e.kind for e in removed[-1:]] == ["removed"] and removed[-1].patch_design_id == "patch_week"
    assert active_awards(removed) == {}
    assert award_views("mara", "demo-mission", "SIM", removed) == ()
    # reached again after the removal
    again = ledger_after(list(range(1, 8)), removed)
    assert [e.kind for e in again[-1:]] == ["earned"] and 7 in active_awards(again)


@pytest.mark.trace("T-E22-038", "DE22-8")
def test_threshold_edit_keeps_old_awards_adds_new_and_a_clock_merge_removes_none() -> None:
    led = ledger_after(list(range(1, 15)), [])
    assert sorted(active_awards(led)) == [7, 14]
    document = config_document()
    edited = dict(document)
    edited["patch_thresholds"] = [5, 10]
    edited["patch_designs"] = [
        {**d, "threshold": t}
        for d, t in zip(document["patch_designs"][:2], (5, 10), strict=True)  # type: ignore[index]
    ]
    new_rules = parse_rules(edited)
    after = ledger_after(list(range(1, 15)), led, rule_set=new_rules)
    assert sorted(active_awards(after)) == [5, 7, 10, 14]  # 5 and 10 added, 7 and 14 kept
    assert active_awards(after)[7] == active_awards(led)[7]  # attribution recorded when earned is kept
    # a rejected release leaves the last good rules; the old rules still produce the same events
    assert ledger_after(list(range(1, 15)), led) == led


@pytest.mark.trace("T-E22-060", "V75")
def test_edited_thresholds_change_awards_for_the_demo_history() -> None:
    demo = list(range(1, 19))
    base = ledger_after(demo, [])
    assert sorted(active_awards(base)) == [7, 14]
    document = config_document()
    edited = dict(document)
    edited["patch_thresholds"] = [5, 10]
    edited["patch_designs"] = [
        {**d, "threshold": t}
        for d, t in zip(document["patch_designs"][:2], (5, 10), strict=True)  # type: ignore[index]
    ]
    changed = ledger_after(demo, [], rule_set=parse_rules(edited))
    assert sorted(active_awards(changed)) == [5, 10]


@pytest.mark.trace("T-E22-035")
def test_award_attribution_failure_is_loud() -> None:
    with pytest.raises(InvalidBasis):
        next_award_events([], total=7, reached={}, rules=rules(), void_applied=False)
