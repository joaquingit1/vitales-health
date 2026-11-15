"""Edge cases of the supplies application that need no station: the pure port helpers, the wire encoder, the bounded
reference calls, the reference checks, step amounts and the release index (E32 3.3, 4.2, 8.1; AGENTS 3.2)."""

from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal

import pytest

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.config import CandidateRelease
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.units import Quantity, Unit
from crew_health.supplies.application.context import BoundedCalls
from crew_health.supplies.application.ports import (
    MissionFacts,
    StockBelowZero,
    StockLevel,
    StockUnitMismatch,
    SupplyInputInvalid,
    SupplyNeed,
    drawn,
    with_added,
)
from crew_health.supplies.application.references import Reference, check_reference
from crew_health.supplies.application.release_index import (
    ReleaseIndexCache,
    SuppliesReleaseCheck,
    build_index,
)
from crew_health.supplies.application.step_uses import step_amounts
from crew_health.supplies.application.wire import Json, encode, instant
from crew_health.supplies.domain.errors import DocumentInvalid, UnitMismatch, UseInvalid

from .harness import SOURCES, document, example, lunar_mission, release

RINSE = Unit("{rinse}")
MISSION: MissionFacts = lunar_mission()


def q(value: object, unit: Unit = RINSE) -> Quantity:
    return Quantity(Decimal(str(value)), unit)


def rinse_need(per_use: Quantity | None, step: str = "eye_rinse") -> SupplyNeed:
    return SupplyNeed("plan_eye_injury", step, "eye_rinse_water", per_use)


def partial_release(*names: ConfigName) -> MemoryRelease:
    return MemoryRelease("release_2", {n: document(n, example(SOURCES[n])) for n in names})


# ---- ports: drawn and with_added ----


@pytest.mark.trace("M3", "drawn", "rule 8")
def test_drawn_refuses_a_level_whose_quantity_is_in_another_unit_than_the_level() -> None:
    level = StockLevel("eye_rinse_water", True, q(6, Unit("{towel}")), RINSE)
    with pytest.raises(StockUnitMismatch) as caught:
        drawn(level, q(1))
    assert caught.value.code == ErrorCode.INVALID_UNIT and caught.value.supply_id == "eye_rinse_water"


@pytest.mark.trace("M3", "drawn", "E32 3")
def test_drawn_refuses_a_difference_past_34_significant_digits_rather_than_rounding_it() -> None:
    level = StockLevel("eye_rinse_water", True, q("1E+40"), RINSE)
    with pytest.raises(SupplyInputInvalid) as caught:
        drawn(level, q("0.5"))
    assert caught.value.field_errors[0].field == "/quantity"
    with pytest.raises(StockBelowZero):  # the same draw on a small level is a plain below zero refusal
        drawn(StockLevel("eye_rinse_water", True, q("0.25"), RINSE), q("0.5"))


@pytest.mark.trace("INV-S11", "rule 5")
@pytest.mark.parametrize("amount", ["0", "-2"])
def test_a_what_if_never_adds_a_quantity_not_above_zero(amount: str) -> None:
    stock = {"eye_rinse_water": StockLevel("eye_rinse_water", True, q(6), RINSE)}
    with pytest.raises(SupplyInputInvalid):
        with_added(stock, [("eye_rinse_water", q(amount))])


# ---- wire ----


@pytest.mark.trace("E32 3", "D3")
def test_a_naive_instant_is_never_written_to_the_wire() -> None:
    with pytest.raises(ValueError, match="naive"):
        instant(datetime(2026, 11, 14, 12, 0))


@pytest.mark.trace("AGENTS 3.1", "AGENTS 3.2")
def test_the_encoder_refuses_nesting_past_its_bound_without_recursion() -> None:
    deep: Json = "leaf"
    for _ in range(16):
        deep = [deep]
    assert encode(deep) == b"[" * 16 + b'"leaf"' + b"]" * 16
    with pytest.raises(ValueError, match="nesting"):
        encode([deep])


# ---- bounded reference calls ----


@pytest.mark.trace("AGENTS 3.2", "E32 3.3 use admission")
def test_a_reference_read_past_the_inflight_bound_is_refused_fast_and_retryable() -> None:
    calls = BoundedCalls(inflight=1, timeout_ms=5000)
    started, finish = threading.Event(), threading.Event()
    results: list[int] = []

    def slow() -> int:
        started.set()
        finish.wait(5)
        return 1

    worker = threading.Thread(target=lambda: results.append(calls.call(slow)))
    worker.start()
    try:
        assert started.wait(5)
        with pytest.raises(ProblemError) as caught:
            calls.call(lambda: 2)
        assert caught.value.code == ErrorCode.INTERNAL_ERROR and caught.value.retryable is True
    finally:
        finish.set()
        worker.join(5)
    assert results == [1]
    for _ in range(500):  # the slot is released by the finished call's callback, so it frees up promptly
        try:
            assert calls.call(lambda: 3) == 3
            break
        except ProblemError:
            threading.Event().wait(0.01)
    else:
        pytest.fail("the slot of a finished call was never released")


# ---- references ----


@pytest.mark.trace("E32 3.3 use admission", "INV-S14")
@pytest.mark.parametrize("step_id", [None, "eye_photo"])
def test_a_plan_run_use_names_a_step_of_the_runs_pinned_plan(step_id: str | None) -> None:
    reference = Reference("plan_run", "SIM", "lunar_surface", {"eye_rinse": (rinse_need(None),)})
    with pytest.raises(UseInvalid) as caught:
        check_reference(reference, MISSION, "eye_rinse_water", step_id)
    assert [i.field for i in caught.value.issues] == ["/step_id"]
    check_reference(reference, MISSION, "eye_rinse_water", "eye_rinse")  # its own step is admitted


# ---- step amounts ----


@pytest.mark.trace("E32 4.2", "INV-S10")
def test_a_step_with_more_needs_than_max_step_uses_is_refused_whole() -> None:
    index = build_index(release())
    uses = [rinse_need(q(1)), rinse_need(q(2))]
    with pytest.raises(UseInvalid) as caught:
        step_amounts(index, index, uses, 1)
    assert [i.field for i in caught.value.issues] == ["/uses"]
    assert [(u.supply.supply_id, u.quantity) for u in step_amounts(index, index, uses, 2)] == [
        ("eye_rinse_water", q(3))
    ]


@pytest.mark.trace("INV-S14", "rule 8")
def test_a_step_need_in_another_unit_than_its_supply_is_refused() -> None:
    index = build_index(release())
    with pytest.raises(UnitMismatch) as caught:
        step_amounts(index, index, [rinse_need(Quantity(Decimal(1), Unit("towel")))], 64)
    assert (caught.value.left, caught.value.right) == ("towel", "{rinse}")


# ---- release index ----


@pytest.mark.trace("E32 8.1", "corrupt or missing configuration")
def test_a_release_without_a_kit_map_or_without_ui_copy_serves_no_cover() -> None:
    with pytest.raises(DocumentInvalid) as no_kit:
        build_index(partial_release(ConfigName.UI_COPY))
    assert no_kit.value.document == "kit-map"
    with pytest.raises(DocumentInvalid) as no_copy:
        build_index(partial_release(ConfigName.KIT_MAP, ConfigName.ONBOARD_PLANS, ConfigName.WARNING_RULES))
    assert (no_copy.value.document, no_copy.value.pointer) == ("ui-copy", "/supply_words")


@pytest.mark.trace("E32 8.1", "INV-S8", "SYSTEM 8.1")
def test_a_release_without_plans_or_warning_rules_has_no_needs_and_no_threshold() -> None:
    index = build_index(partial_release(ConfigName.KIT_MAP, ConfigName.UI_COPY))
    assert index.steps == () and index.plans_version is None
    assert index.threshold is None  # with no warning rules nothing is ever low
    assert index.needs_in("lunar_surface") == {}
    assert "eye_rinse_water" in index.supplies


@pytest.mark.trace("AGENTS 3.2", "supplies.release_cache")
def test_the_release_cache_holds_at_least_one_release() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        ReleaseIndexCache(0)


@pytest.mark.trace("E32 8.3")
def test_the_release_check_has_nothing_to_say_about_a_candidate_without_a_kit_map() -> None:
    copy_only = {ConfigName.UI_COPY: document(ConfigName.UI_COPY, example(SOURCES[ConfigName.UI_COPY]))}
    assert SuppliesReleaseCheck().check(CandidateRelease("release_9", copy_only)) == ()
