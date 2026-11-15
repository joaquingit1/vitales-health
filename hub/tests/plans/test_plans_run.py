"""PlanRun matrix, ordered guards, internal reconciliation and invariants."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import Actor
from crew_health.plans.domain.errors import (
    InvalidField,
    InvalidTransition,
    PlansError,
    StaleVersion,
    StepNotAvailable,
)
from crew_health.plans.domain.model import Needs, Plan, RecheckSpec, SupplyNeed
from crew_health.plans.domain.run import (
    ActionContext,
    Close,
    Escalate,
    EscalationClosed,
    EscalationSeen,
    GuidedCheckDone,
    HandoffLinked,
    LinkAtOpen,
    NotAvailable,
    PlanRun,
    RunCommand,
    StepDone,
    UseSubstitute,
    actionable,
    apply,
    derive_state,
    open_run,
)

from .harness import now, plan


def opened(p: Plan | None = None) -> tuple[PlanRun, ActionContext]:
    p = plan() if p is None else p
    actor = Actor("person", "helper")
    run = open_run(
        plan=p,
        environment_id="lunar_surface",
        plan_run_id="pr_1",
        pins=(("ui-copy", "u"), ("onboard-plans", "p")),
        opened_release_id="release",
        subject_person_id="subject",
        episode_id="ep_1",
        help_session_id=None,
        origin_mode="SIM",
        opened_by=actor,
        at=now(),
        link=LinkAtOpen("down", "outage", None),
        return_option_present=True,
    )
    ctx = ActionContext(
        p,
        {s.step_id: True for s in run.steps},
        {s.step_id: "aboard" for s in run.steps},
        actor,
        now(),
        False,
        2,
        2,
        30,
    )
    return run, ctx


def checked(
    run: PlanRun, cmd: RunCommand, ctx: ActionContext, changed: bool, event: str | None = None
) -> PlanRun:
    result, effects = apply(run, cmd, ctx)
    assert result.revision == run.revision + int(changed)
    assert result.state == derive_state(result)
    if changed:
        assert effects.event == (event or "plan_run.changed") and effects.outcome == "applied"
    else:
        assert result is run and effects.event is None
    return result


@pytest.mark.trace("T28-10", "PR-I4", "PR-I2", "PR-I3", "EI11")
def test_open_pins_steps_substitutes_and_return_option() -> None:
    run, ctx = opened()
    assert run.revision == 1 and run.state == "active"
    assert run.pins == (("onboard-plans", "p"), ("ui-copy", "u"))
    assert all(s.state == "open" and s.runs == 0 for s in run.steps)
    assert run.steps[5].principal_step_id == run.steps[4].step_id
    assert not actionable(run, run.steps[5])
    assert all(r.due_at is None and r.done_at is None for r in run.rechecks)
    for episode, help_id in ((None, None), ("ep", "help")):
        with pytest.raises(InvalidField) as exc:
            open_run(
                plan=ctx.plan,
                environment_id="lunar_surface",
                plan_run_id="pr",
                pins=(),
                opened_release_id="r",
                subject_person_id="subject",
                episode_id=episode,
                help_session_id=help_id,
                origin_mode="LIVE",
                opened_by=ctx.actor,
                at=now(),
                link=run.link_state_at_open,
                return_option_present=False,
            )
        assert exc.value.pointer == "/episode_id"
    eye = plan("plan_eye_injury")
    for option in (False, True):
        r = open_run(
            plan=eye,
            environment_id="lunar_surface",
            plan_run_id="pr",
            pins=(),
            opened_release_id="r",
            subject_person_id="subject",
            episode_id=None,
            help_session_id="help",
            origin_mode="LIVE",
            opened_by=ctx.actor,
            at=now(),
            link=run.link_state_at_open,
            return_option_present=option,
        )
        assert any(s.stage_id == "return" for s in r.steps) == option


@pytest.mark.trace("T28-10", "PR-I4", "T28-13")
def test_public_step_matrix_every_cell() -> None:
    initial, ctx = opened()
    sid = initial.steps[4].step_id  # principal with a substitute and recheck
    for state in ("open", "done", "not_available", "substituted"):
        if state == "open":
            run = initial
        elif state == "done":
            run = checked(initial, StepDone(sid, (), None, 1), ctx, True, "plan_run.step_done")
        elif state == "not_available":
            run = checked(initial, NotAvailable(sid, "broken", 1), ctx, True)
        else:
            run = checked(initial, UseSubstitute(sid, 1), ctx, True)
        if state in ("open", "done"):
            result = checked(
                run, StepDone(sid, ("obs",), None, run.revision), ctx, True, "plan_run.step_done"
            )
            assert result.step(sid) is not None
        else:
            with pytest.raises(InvalidTransition):
                apply(run, StepDone(sid, (), None, 1), ctx)
        if state in ("open", "not_available"):
            checked(run, NotAvailable(sid, "broken", 1), ctx, state == "open")
        else:
            with pytest.raises(InvalidTransition):
                apply(run, NotAvailable(sid, "broken", 1), ctx)
        if state == "done":
            with pytest.raises(InvalidTransition):
                apply(run, UseSubstitute(sid, 1), ctx)
        else:
            result = checked(run, UseSubstitute(sid, 1), ctx, state != "substituted")
            assert actionable(result, result.steps[5]) and result.steps[5].state == "open"
        escalated = checked(
            run, Escalate("icl10_worse_chest", "help", None, run.revision), ctx, True, "plan_run.escalated"
        )
        assert escalated.state == "escalated"
        checked(escalated, Escalate("icl10_worse_chest", "help", None, escalated.revision), ctx, False)
        closed = checked(run, Close("other", None, run.revision), ctx, True)
        assert closed.state == "closed"
    normal = initial.steps[1].step_id
    done = checked(initial, StepDone(normal, (), None, 1), ctx, True, "plan_run.step_done")
    checked(done, StepDone(normal, (), None, 1), ctx, False)
    with pytest.raises(InvalidField, match="no_substitute"):
        apply(initial, UseSubstitute(normal, 1), ctx)
    with pytest.raises(InvalidField, match="not_escalation_step"):
        apply(initial, Escalate(normal, "h", None, 1), ctx)


@pytest.mark.trace("T28-10", "T28-13", "R28-18", "R28-14", "EI7")
def test_guard_order_closed_membership_restricted_stale_then_cell() -> None:
    run, ctx = opened()
    closed = checked(run, Close("other", None, 1), ctx, True)
    commands: tuple[RunCommand, ...] = (
        StepDone("missing", (), None, 0),
        NotAvailable("missing", "x", 0),
        UseSubstitute("missing", 0),
        Escalate("missing", "h", None, 0),
        Close("other", None, 0),
    )
    for cmd in commands:
        with pytest.raises(InvalidTransition, match="run_closed"):
            apply(closed, cmd, replace(ctx, restricted=True))
    with pytest.raises(InvalidField, match="not_in_run"):
        apply(run, StepDone("missing", (), None, 0), replace(ctx, restricted=True))
    for cmd in (
        StepDone(run.steps[1].step_id, (), None, 0),
        NotAvailable(run.steps[1].step_id, "x", 0),
        UseSubstitute(run.steps[4].step_id, 0),
    ):
        with pytest.raises(InvalidTransition, match="run_restricted"):
            apply(run, cmd, replace(ctx, restricted=True))
    for cmd in (
        Close("other", None, 0),
        Escalate("icl10_worse_chest", "h", None, 0),
        Close("other", None, 2),
        Escalate("icl10_worse_chest", "h", None, 7),
    ):
        with pytest.raises(StaleVersion):
            apply(run, cmd, replace(ctx, restricted=True))
    checked(run, Close("other", None, 1), replace(ctx, restricted=True), True)
    checked(
        run,
        Escalate("icl10_worse_chest", "h", None, 1),
        replace(ctx, restricted=True),
        True,
        "plan_run.escalated",
    )
    with pytest.raises(InvalidTransition, match="substitute_not_actionable"):
        apply(run, StepDone(run.steps[5].step_id, (), None, 1), ctx)
    for altered in (
        replace(ctx, runnable={}),
        replace(ctx, availability={run.steps[4].step_id: "substituted"}),
    ):
        with pytest.raises(StepNotAvailable):
            apply(run, StepDone(run.steps[4].step_id, (), None, 1), altered)
    # All three commuting actions accept older If-Match values.
    for cmd in (
        StepDone(run.steps[1].step_id, (), None, 0),
        NotAvailable(run.steps[1].step_id, "x", 0),
        UseSubstitute(run.steps[4].step_id, 0),
    ):
        result, effects = apply(run, cmd, ctx)
        assert result.revision == 2 and effects.event is not None


@pytest.mark.trace("T28-14", "R28-15", "R28-20", "PR-I8")
def test_rechecks_retry_bounds_supply_refusal_and_single_debit() -> None:
    p = plan()
    sid = p.steps[4].step_id
    spec = RecheckSpec(None, None, 60, "sourced", None)
    p = replace(p, steps=tuple(replace(s, recheck=spec) if s.step_id == sid else s for s in p.steps))
    run, ctx = opened(p)
    done = checked(run, StepDone(sid, ("obs1",), None, 1), ctx, True, "plan_run.step_done")
    assert done.rechecks[0].done_at == now() and done.rechecks[0].due_at == now() + timedelta(seconds=60)
    checked(done, StepDone(sid, (), None, 1), ctx, False)
    with pytest.raises(StepNotAvailable):
        apply(done, StepDone(sid, (), None, 2), replace(ctx, runnable={}))
    second = checked(done, StepDone(sid, ("obs2",), None, 2), ctx, True, "plan_run.step_done")
    third = checked(second, StepDone(sid, (), None, 3), ctx, True, "plan_run.step_done")
    with pytest.raises(InvalidField, match="recheck_bound"):
        apply(third, StepDone(sid, (), None, 4), ctx)
    need = SupplyNeed(p.plan_id, sid, "supply", Quantity(Decimal(1), Unit("1")), True, True)
    p = replace(
        p, steps=tuple(replace(s, needs=Needs((), (need,), ())) if s.step_id == sid else s for s in p.steps)
    )
    run, ctx = opened(p)
    done, effects = apply(run, StepDone(sid, (), None, 1), ctx)
    assert (
        len(effects.debits) == 1 and effects.debits[0].occurrence == 1 and effects.debits[0].needs == (need,)
    )
    with pytest.raises(StepNotAvailable):
        apply(done, StepDone(sid, (), None, 2), ctx)
    checked(done, StepDone(sid, (), None, 1), ctx, False)


@pytest.mark.trace("T28-10", "PR-I4", "SYSTEM-8.1", "R28-18")
def test_every_internal_command_and_closed_reconciliation() -> None:
    run, ctx = opened()
    sid = run.steps[0].step_id
    result, effects = apply(run, GuidedCheckDone(sid, "g", "disagree", ("obs",)), ctx)
    assert result is run and effects.outcome == "guided_check_disagree"
    for outcome in ("agree", "no_reference"):
        cmd = GuidedCheckDone(sid, "g", outcome, ("obs",))
        done = checked(run, cmd, replace(ctx, restricted=True), True, "plan_run.step_done")
        checked(done, cmd, ctx, False)
        result, effects = apply(done, replace(cmd, guided_check_id="different"), ctx)
        assert result is done and effects.outcome == "guided_check_not_applied"
    for cmd in (
        GuidedCheckDone("missing", "g", "agree", ()),
        GuidedCheckDone(run.steps[5].step_id, "g", "agree", ()),
    ):
        result, effects = apply(run, cmd, ctx)
        assert result is run and effects.outcome == "guided_check_not_applied"
    result, effects = apply(run, GuidedCheckDone(sid, "g", "agree", ()), replace(ctx, runnable={}))
    assert result is run and effects.outcome == "guided_check_not_applied"
    result, effects = apply(run, EscalationSeen(sid, "h", None), ctx)
    assert result is run and effects.outcome == "escalation_not_applied"
    closed = checked(run, Close("other", None, 1), ctx, True)
    result, effects = apply(closed, GuidedCheckDone(sid, "g", "agree", ()), ctx)
    assert result is closed and effects.outcome == "guided_check_not_applied"
    attributed = checked(
        closed,
        EscalationSeen("icl10_worse_chest", "h", "escalation"),
        replace(ctx, max_escalations_per_run=0),
        True,
        "plan_run.escalated",
    )
    assert attributed.state == "closed"
    checked(attributed, EscalationSeen("icl10_worse_chest", "h", None), ctx, False)
    ended = checked(attributed, EscalationClosed("h"), ctx, True)
    assert ended.escalations[0].session_closed and ended.state == "closed"
    checked(ended, EscalationClosed("missing"), ctx, False)
    linked = checked(ended, HandoffLinked("handoff"), ctx, True)
    checked(linked, HandoffLinked("handoff"), ctx, False)
    result, effects = apply(linked, HandoffLinked("other"), ctx)
    assert result is linked and effects.outcome == "handoff_conflict"
    live = checked(run, EscalationSeen("icl10_worse_chest", "h", None), ctx, True, "plan_run.escalated")
    assert live.state == "escalated"
    assert checked(live, EscalationClosed("h"), ctx, True).state == "active"
    with pytest.raises(InvalidField, match="escalation_bound"):
        apply(run, Escalate("icl10_worse_chest", "h", None, 1), replace(ctx, max_escalations_per_run=0))


@pytest.mark.trace("T28-11", "PR-I1", "PR-I2", "PR-I3", "PR-I4", "EI5")
@given(st.lists(st.tuples(st.integers(0, 8), st.integers(0, 20), st.booleans()), max_size=50))
def test_random_sequences_preserve_derived_state_revision_and_closed_final(
    sequence: list[tuple[int, int, bool]],
) -> None:
    run, ctx = opened()
    original_ids = tuple(s.step_id for s in run.steps)
    for kind, index, stale in sequence:
        sid = run.steps[index % len(run.steps)].step_id
        revision = 0 if stale else run.revision
        commands: tuple[RunCommand, ...] = (
            StepDone(sid, (), None, revision),
            NotAvailable(sid, "broken", revision),
            UseSubstitute(sid, revision),
            Escalate(sid, "h" + str(index), None, revision),
            Close("other", None, revision),
            GuidedCheckDone(sid, "g", "agree", ()),
            EscalationSeen(sid, "h" + str(index), None),
            EscalationClosed("h" + str(index)),
            HandoffLinked("handoff"),
        )
        old = run
        try:
            run, effects = apply(old, commands[kind], ctx)
        except PlansError:
            continue
        assert run.state == derive_state(run)
        assert run.revision == old.revision + int(effects.event is not None)
        assert bool(run.episode_id) != bool(run.help_session_id)
        assert tuple(s.step_id for s in run.steps) == original_ids
        if old.close is not None:
            assert run.state == "closed" and run.close == old.close


@pytest.mark.trace("T28-11", "PR-I1", "EI5")
def test_completing_initial_work_enters_watching_without_a_ground_link() -> None:
    run, ctx = opened()
    for step in run.steps:
        if step.stage_id in ("check_another_way", "do_now") and not step.substitute_only:
            run = checked(
                run, StepDone(step.step_id, (), None, run.revision), ctx, True, "plan_run.step_done"
            )
    assert run.state == "watching" and run.handoff_id is None
    escalated = checked(run, EscalationSeen("icl10_worse_chest", "h", None), ctx, True, "plan_run.escalated")
    assert checked(escalated, EscalationClosed("h"), ctx, True).state == "watching"


@pytest.mark.trace("T28-10", "PR-I4", "PR-I8", "SYSTEM-8.1")
def test_guided_completion_debits_once_and_rejects_nonopen_states() -> None:
    p = plan("plan_eye_injury")
    run, ctx = opened(p)
    cmd = GuidedCheckDone("eye_object_shield", "g", "no_reference", ("obs",))
    done, effects = apply(run, cmd, ctx)
    assert effects.event == "plan_run.step_done" and len(effects.debits) == 1
    assert effects.debits[0].occurrence == 1
    checked(done, cmd, ctx, False)
    for state_cmd in (NotAvailable("eye_object_shield", "broken", 1),):
        changed, _ = apply(run, state_cmd, ctx)
        result, effects = apply(changed, cmd, ctx)
        assert result is changed and effects.debits == () and effects.outcome == "guided_check_not_applied"
