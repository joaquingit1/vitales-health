"""People-independent planning and deterministic stage views."""

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.plans.domain.model import STAGE_ORDER, Needs, Who
from crew_health.plans.domain.view import (
    needs_medical_officer,
    plan_aboard,
    return_applies,
    view_stages,
    waits_for_medical_officer,
)

from .harness import context, plan


@pytest.mark.trace("V82", "EI3", "R57")
def test_aboard_requires_any_crew_skill_free_step_and_monitoring() -> None:
    p, ctx = plan(), context()
    assert plan_aboard(p, replace(ctx, people=None))
    assert not plan_aboard(p, replace(ctx, environment_id="unknown"))
    officers = replace(
        p,
        steps=tuple(
            replace(s, who=Who("crew_medical_officer", None)) if s.who.kind == "any_crew" else s
            for s in p.steps
        ),
    )
    assert not plan_aboard(officers, ctx)
    assert not plan_aboard(replace(p, steps=(p.steps[1],)), ctx)
    assert not plan_aboard(replace(p, steps=(p.steps[5],)), ctx)  # substitute cannot count on its own
    only_camera = replace(p, steps=p.steps[4:6])
    assert plan_aboard(only_camera, replace(ctx, kit=frozenset(("pulse_oximeter",))))
    assert not plan_aboard(only_camera, replace(ctx, kit=None))
    assert not plan_aboard(
        replace(
            only_camera,
            steps=tuple(
                replace(s, needs=Needs(s.needs.instruments, (), ("skill",))) for s in only_camera.steps
            ),
        ),
        ctx,
    )


@pytest.mark.trace("R28-6", "R57")
def test_waits_for_officer_considers_equipped_applicable_non_substitutes() -> None:
    p, ctx = plan(), context()
    assert not waits_for_medical_officer(p, ctx)
    officer = replace(p.steps[1], who=Who("crew_medical_officer", None))
    assert waits_for_medical_officer(replace(p, steps=(officer,)), ctx)
    assert not waits_for_medical_officer(replace(p, steps=()), ctx)
    assert needs_medical_officer(p.steps[0], ctx)
    assert not needs_medical_officer(p.steps[0], replace(ctx, people=None))
    people = tuple(replace(x, officer_rank=None) for x in ctx.people or ())
    assert not needs_medical_officer(p.steps[0], replace(ctx, people=people))
    assert not waits_for_medical_officer(replace(p, steps=(replace(officer, substitute_only=True),)), ctx)
    assert not waits_for_medical_officer(
        replace(p, steps=(replace(officer, needs=Needs(("absent",), (), ())),)), ctx
    )


@pytest.mark.trace("T28-26", "V94", "EI11")
def test_return_requires_option_and_listed_environment() -> None:
    p, ctx = plan("plan_eye_injury"), context()
    assert return_applies(p, ctx)
    assert "return" in tuple(s.stage_id for s in view_stages(p, ctx))
    assert not return_applies(p, replace(ctx, return_option_present=False))
    assert not return_applies(p, replace(ctx, environment_id="mars_surface"))
    assert not return_applies(plan(), ctx)


@pytest.mark.trace("T28-21", "T28-39", "EI4")
@given(st.permutations(STAGE_ORDER))
def test_stage_order_and_ground_log_are_stable_despite_document_permutations(order: list[str]) -> None:
    p, ctx = plan("plan_eye_injury"), context()
    ranks = {name: i for i, name in enumerate(order)}
    p = replace(p, steps=tuple(sorted(p.steps, key=lambda s: ranks[s.stage_id])))
    result = view_stages(p, ctx)
    ids = tuple(s.stage_id for s in result)
    assert ids == tuple(kind for kind in STAGE_ORDER if kind in ids)
    assert ids[-1] == "ground_log"
    assert result == view_stages(p, ctx)
