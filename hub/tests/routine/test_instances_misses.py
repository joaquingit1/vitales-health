from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from crew_health.routine.domain.instances import (
    QuestionInstance,
    apply_answer,
    expire,
    pick_instance,
    plan_slots,
)
from crew_health.routine.domain.misses import MissFact, summarize, window_for
from crew_health.routine.domain.release import AddQuestions

from .support import NOW, release


def day_end(at: datetime) -> datetime:
    return at.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def instance() -> QuestionInstance:
    r = release()
    b = r.bindings[0]
    assert isinstance(b.effect, AddQuestions) and r.question_sets is not None
    return QuestionInstance(
        "rq_1",
        "rov_1",
        "p_test",
        b.effect.question_set_id,
        "initial",
        r.question_sets[b.effect.question_set_id],
        NOW,
        day_end(NOW),
        {},
        "open",
        1,
    )


@pytest.mark.trace("D16", "T-E25-032", "INV-E25-18")
def test_real_co2_binding_plans_now_and_two_hours_later_and_each_question_needs_answer() -> None:
    r = release()
    effect = r.bindings[0].effect
    assert isinstance(effect, AddQuestions)
    slots = plan_slots(source_at=NOW, repeat_after_minutes=effect.repeat_after_minutes, day_end_of=day_end)
    # D16: "now and 2 hours later" is an independent golden expectation.
    assert slots == (("initial", NOW, day_end(NOW)), ("repeat:0", NOW + timedelta(hours=2), day_end(NOW)))
    inst = instance()
    first = apply_answer(inst, question_id=inst.question_ids[0], answer_id="ans_1", answered_at=NOW)
    assert first is not None and first.state == "open"
    second = apply_answer(first, question_id=inst.question_ids[1], answer_id="ans_2", answered_at=NOW)
    assert second is not None and second.state == "answered" and second.revision == 3
    assert apply_answer(second, question_id=inst.question_ids[0], answer_id="other", answered_at=NOW) is None
    assert inst.state == "open" and not inst.answers


@pytest.mark.trace("T-E25-045", "INV-E25-18")
def test_partial_answers_expire_without_parent_completion_and_bounds_are_half_open() -> None:
    inst = instance()
    partial = apply_answer(inst, question_id=inst.question_ids[0], answer_id="ans_1", answered_at=NOW)
    assert partial is not None
    assert expire(partial, now=inst.due_until).state == "partially_answered"
    assert expire(inst, now=inst.due_until).state == "unanswered"
    assert expire(inst, now=NOW) == inst
    assert (
        apply_answer(inst, question_id=inst.question_ids[0], answer_id="ans_1", answered_at=inst.due_until)
        is None
    )
    assert (
        apply_answer(
            inst, question_id=inst.question_ids[0], answer_id="ans_1", answered_at=NOW - timedelta(seconds=1)
        )
        is None
    )
    assert apply_answer(partial, question_id=inst.question_ids[0], answer_id="ans_2", answered_at=NOW) is None
    assert apply_answer(inst, question_id="absent", answer_id="ans_1", answered_at=NOW) is None


@pytest.mark.trace("T-E25-032")
def test_repeat_crossing_midnight_has_its_own_day_and_pick_uses_time_then_id() -> None:
    effect = release().bindings[0].effect
    assert isinstance(effect, AddQuestions)
    late = NOW + timedelta(hours=23)
    slots = plan_slots(source_at=late, repeat_after_minutes=effect.repeat_after_minutes, day_end_of=day_end)
    assert slots[1][1] == NOW + timedelta(days=1, hours=1)
    assert slots[1][2] == NOW + timedelta(days=2)
    inst = instance()
    later_inst = replace(inst, instance_id="rq_2", due_from=NOW + timedelta(hours=1))
    tie = replace(inst, instance_id="rq_0")
    assert pick_instance((later_inst, inst, tie), inst.question_ids[0], NOW + timedelta(hours=2)) == tie
    assert pick_instance((inst,), inst.question_ids[0], inst.due_until) is None


@pytest.mark.trace("V67", "T-E25-011", "INV-E25-06")
def test_weekly_misses_fourteen_and_twenty_one_qualify_but_daily_eight_days_apart_do_not() -> None:
    r = release()
    weekly = next(i for i in r.items if i.default_cadence.word == "weekly")
    daily = next(i for i in r.items if i.default_cadence.word == "daily")
    # VALIDATION V67, E25 3.11: weekly days 14 and 21 qualify, daily span eight does not.
    facts = (
        MissFact(weekly.item_id, 14, True),
        MissFact(weekly.item_id, 21, True),
        MissFact(daily.item_id, 14, True),
        MissFact(daily.item_id, 22, True),
    )
    summary = summarize(facts, r.misses, {weekly.item_id: 7, daily.item_id: 1}, None)
    assert (summary.count, summary.barrier_question_due, summary.item_ids, summary.window_days) == (
        2,
        True,
        (weekly.item_id,),
        7,
    )
    assert window_for(r.misses, 30) == 30
    assert window_for(r.misses, None) == 7


@pytest.mark.trace("T-E25-011", "INV-E25-06")
def test_uncounted_misses_never_count_and_ask_resets_history() -> None:
    r = release()
    item_id = r.items[0].item_id
    facts = (MissFact(item_id, 14, True), MissFact(item_id, 15, True), MissFact(item_id, 16, False))
    assert summarize(facts, r.misses, {item_id: 1}, None).count == 2
    assert summarize(facts, r.misses, {item_id: 1}, 14).count == 0
    assert (
        summarize(
            tuple(replace(f, counted=False) for f in facts), r.misses, {item_id: 1}, None
        ).barrier_question_due
        is False
    )
