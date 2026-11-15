"""C22 rules 1 to 7 and fail-safe availability properties."""

from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.units import Quantity, Unit
from crew_health.plans.domain.availability import (
    Person,
    StockLevel,
    assess_step,
    equipment_missing,
    runnable_now,
)
from crew_health.plans.domain.model import Needs, SupplyNeed, Who

from .harness import context, plan


@pytest.mark.trace("R57", "EI7", "T28-02")
def test_substituted_only_when_the_substitute_itself_can_run() -> None:
    p, ctx = plan(), context()
    principal = p.steps[4]
    sub = p.step("icl10_pulse_at_oximeter")
    assert sub is not None
    skilled = replace(
        p,
        steps=tuple(
            replace(s, needs=Needs(s.needs.instruments, (), ("cpr",))) if s is sub else s for s in p.steps
        ),
    )
    lunar = replace(ctx, kit=frozenset(("pulse_oximeter",)))
    assert assess_step(principal, skilled, lunar).availability == "not_aboard"
    trained = replace(
        lunar, people=(*(lunar.people or ()), Person("crew3", "Crew", 2, None, True, {"cpr": "fresh"}))
    )
    assert assess_step(principal, skilled, trained).availability == "substituted"


@pytest.mark.trace("R57", "EI7", "T28-02", "T28-07", "T28-22", "T28-23")
def test_equipment_substitutes_unknowns_and_units() -> None:
    p, ctx = plan(), context()
    s = p.steps[4]
    assert assess_step(s, p, replace(ctx, kit=frozenset())).availability == "not_aboard"
    a = assess_step(s, p, replace(ctx, kit=frozenset(("pulse_oximeter",))))
    assert a.availability == "substituted" and a.substitute_step_id == "icl10_pulse_at_oximeter"
    a = assess_step(s, p, replace(ctx, kit=None))
    assert a.missing_instruments == ("camera_station",) and a.unknown == ("kit_unknown",)
    need = SupplyNeed(p.plan_id, s.step_id, "supply", Quantity(Decimal(2), Unit("1")), True, True)
    s = replace(s, needs=Needs((), (need,), ()))
    assert equipment_missing(s, replace(ctx, stock=None)) == ((), ("supply",), ("stock_unknown",))
    for stock in (
        {},
        {"supply": StockLevel("supply", False, None)},
        {"supply": StockLevel("supply", True, Quantity(Decimal(0), Unit("1")))},
        {"supply": StockLevel("supply", True, Quantity(Decimal(1), Unit("1")))},
    ):
        assert equipment_missing(s, replace(ctx, stock=stock))[1] == ("supply",)
    a = assess_step(
        s, p, replace(ctx, stock={"supply": StockLevel("supply", True, Quantity(Decimal(3), Unit("mL")))})
    )
    assert a.missing_supplies == ("supply",) and a.unknown == ("unit_mismatch:supply",)
    a = assess_step(
        s,
        p,
        replace(
            ctx,
            stock={"supply": StockLevel("supply", True, Quantity(Decimal(2), Unit("1")))},
            cover_after={(s.step_id, "supply"): 4},
        ),
    )
    assert a.availability == "aboard" and a.cover_after == (("supply", 4),)
    assert equipment_missing(replace(s, needs=Needs((), (replace(need, known=False),), ())), ctx)[1] == (
        "supply",
    )


@pytest.mark.trace("R57", "EI7", "T28-07", "T28-22", "T28-23")
def test_holders_subject_availability_and_performer_order() -> None:
    p, ctx = plan(), context()
    s = p.steps[0]
    only_subject = replace(ctx, people=(Person("subject", None, 0, 1, True, {"pulse_check_wrist": "fresh"}),))
    assert assess_step(s, p, only_subject).availability == "needs_skill_holder"
    assert assess_step(replace(s, who=Who("crew_medical_officer", None)), p, only_subject).who_can == ()
    asleep = replace(ctx, people=tuple(replace(person, available=False) for person in ctx.people or ()))
    a = assess_step(s, p, asleep)
    assert a.availability == "aboard" and not a.who_can and not runnable_now(a)
    a = assess_step(s, p, replace(ctx, people=None))
    assert a.missing_skills == ("pulse_check_wrist",) and a.unknown == ("people_unknown",)
    people = (
        Person("b", None, 2, 1, True, {"pulse_check_wrist": "due"}),
        Person("a", None, 2, 2, True, {"pulse_check_wrist": "stale"}),
        Person("subject", None, 0, 0, True, {}),
        Person("c", None, 1, 2, True, {}),
    )
    for who, ids in (
        (Who("any_crew", None), ("subject", "c", "a", "b")),
        (Who("crew_medical_officer", None), ("b", "c", "a")),
        (Who("skill", "pulse_check_wrist"), ("b",)),
    ):
        a = assess_step(replace(s, who=who, needs=Needs((), (), ())), p, replace(ctx, people=people))
        assert tuple(x.person_id for x in a.who_can) == ids


@pytest.mark.trace("R57", "EI7", "T28-22")
@given(
    st.sets(st.sampled_from(("camera_station", "pulse_oximeter"))),
    st.integers(min_value=0, max_value=5),
    st.booleans(),
    st.booleans(),
)
def test_random_kits_stocks_and_needs_never_claim_missing_equipment_aboard(
    kit: set[str],
    stock: int,
    needs_supply: bool,
    unknown: bool,
) -> None:
    p, ctx = plan(), context()
    s = p.steps[4]
    need = SupplyNeed(p.plan_id, s.step_id, "supply", Quantity(Decimal(2), Unit("1")), True, True)
    s = replace(s, needs=Needs(("camera_station",), (need,) if needs_supply else (), ()))
    ctx = replace(
        ctx,
        kit=None if unknown else frozenset(kit),
        stock={"supply": StockLevel("supply", True, Quantity(Decimal(stock), Unit("1")))},
    )
    a = assess_step(s, p, ctx)
    if a.missing_instruments or a.missing_supplies or a.missing_skills:
        assert a.availability != "aboard"
    if a.availability == "aboard":
        assert not (a.missing_instruments or a.missing_supplies or a.missing_skills)
    if a.availability == "substituted":
        sub = p.step(a.substitute_step_id or "")
        assert sub is not None and equipment_missing(sub, ctx) == ((), (), ())
    if runnable_now(a):
        assert a.applies and a.availability == "aboard"
