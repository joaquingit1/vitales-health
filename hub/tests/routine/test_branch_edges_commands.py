"""Command, query and provided port edges: refusals, privacy on replay, bounded reads and pinned items."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from crew_health.kernel.uow import Actor
from crew_health.routine.application.ports import OccurrenceActionBody, OverrideCreateBody, OverrideUntil
from crew_health.routine.domain.errors import (
    ActionNotAllowed,
    ConfigInvalid,
    InvalidTransition,
    MissionUnavailable,
    NotFound,
    RecheckRejected,
    Unauthorized,
)
from crew_health.routine.domain.occurrence import Occurrence
from crew_health.routine.domain.windows import policy_window

from .harness import RoutineHarness, session
from .test_commands_provided import add_recheck, manual_body, morning, occurrence_id, officer

ENGINE = Actor("engine", "engine_routine")


def tight(hub: RoutineHarness, **limits: int) -> None:
    deps = hub.engine.deps
    hub.restart(replace(deps, settings=replace(deps.settings, **limits)))


def tomorrow_occurrence(hub: RoutineHarness, item: str = "exercise_log") -> Occurrence:
    with hub.db.begin(actor=ENGINE, correlation_id="cor_tomorrow") as uow:
        release, mission = hub.engine.release(uow), hub.engine.mission(uow)
        today, version = hub.engine.day(uow.now)
        window = policy_window(release.policy(item), *hub.engine.bounds(today + 1, version))
        assert window is not None
        occ = hub.engine.make_occurrence(
            uow, "person_mara", item, window, "cadence_event:test", "cadence_event", mission, release
        )
        assert hub.engine.insert(uow, occ)
        return occ


def stored_with_unknown_item(hub: RoutineHarness) -> str:
    identifier = occurrence_id(hub, "daily_check")
    with hub.db.begin(actor=ENGINE, correlation_id="cor_unknown_item") as uow:
        occ = hub.engine.deps.store.get(uow, identifier)
        assert occ is not None
        orphan = replace(
            occ, occurrence_id="ro_orphan", item_id="item_withdrawn", cause_key="cadence_event:orphan"
        )
        assert hub.engine.deps.store.insert(uow, orphan)
    identifier_of_orphan: str = orphan.occurrence_id
    return identifier_of_orphan


def test_work_that_is_not_due_yet_cannot_be_acted_on(tmp_path: Path) -> None:
    hub = morning(tmp_path)
    occ = tomorrow_occurrence(hub)
    assert occ.state == "not_due"
    with pytest.raises(InvalidTransition):
        hub.commands.act(
            session(),
            occ.occurrence_id,
            OccurrenceActionBody("complete"),
            if_match=1,
            idempotency_key="cmd_early",
        )
    assert hub.rows(
        "SELECT state,revision FROM routine_occurrence WHERE occurrence_id=?", (occ.occurrence_id,)
    ) == (("not_due", 1),)


def test_completion_links_only_answers_of_the_same_person(tmp_path: Path) -> None:
    hub = morning(tmp_path)
    with hub.db.snapshot() as view:
        question = hub.engine.release(view).misses.question_id
    hub.answer(question, answer_id="answer_own")
    hub.answer(question, person_id="person_tomas", answer_id="answer_other")
    identifier = occurrence_id(hub, "daily_check")
    with pytest.raises(Unauthorized) as error:
        hub.commands.act(
            session(),
            identifier,
            OccurrenceActionBody("complete", answer_ids=("answer_other",)),
            if_match=1,
            idempotency_key="cmd_foreign_answer",
        )
    assert error.value.reason == "linked_record_scope"
    status, body = hub.commands.act(
        session(),
        identifier,
        OccurrenceActionBody("complete", answer_ids=("answer_own",)),
        if_match=1,
        idempotency_key="cmd_own_answer",
    )
    assert status == 200 and body["state"] == "done"
    assert cast(dict[str, object], body["outcome"])["answer_ids"] == ["answer_own"]


def test_the_assistant_cannot_create_a_manual_override(tmp_path: Path) -> None:
    hub = morning(tmp_path)
    assistant = replace(officer(), actor=Actor("assistant", "assistant_1"))
    with pytest.raises(Unauthorized) as error:
        hub.commands.create_override(assistant, manual_body(), idempotency_key="cmd_assistant")
    assert error.value.reason == "human_required"
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((0,),)


def test_a_manual_override_refuses_a_roster_larger_than_its_bound(tmp_path: Path) -> None:
    hub = morning(tmp_path)
    tight(hub, max_roster=2)
    with pytest.raises(ActionNotAllowed) as error:
        hub.commands.create_override(officer(), manual_body(), idempotency_key="cmd_roster")
    assert error.value.reason == "capacity"
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((0,),)


def test_an_override_replay_needs_a_current_care_grant(tmp_path: Path) -> None:
    hub = morning(tmp_path)
    body = OverrideCreateBody("vision_self_check", 0, "person_tomas", OverrideUntil("person_stops"), None)
    status, created = hub.commands.create_override(officer(), body, idempotency_key="cmd_for_tomas")
    assert status == 201
    with pytest.raises(NotFound):
        hub.commands.create_override(officer(), body, idempotency_key="cmd_for_tomas")
    hub.grant_officer_care()
    assert hub.commands.create_override(officer(), body, idempotency_key="cmd_for_tomas") == (201, created)
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((1,),)


def test_a_pending_barrier_question_joins_the_daily_check(tmp_path: Path) -> None:
    hub = morning(tmp_path)
    assert hub.runtime.added_questions("person_mara", 1, hub.clock.now()) == ()
    with hub.db.begin(actor=ENGINE, correlation_id="cor_barrier") as uow:
        hub.engine.deps.store.put_barrier_ask(
            uow, "person_mara", 1, ("daily_check",), "pending", None, uow.now
        )
        question = hub.engine.release(uow).misses.question_id
    added = tuple(hub.runtime.added_questions("person_mara", 1, hub.clock.now()))
    assert [(q.question_id, q.cause, q.repeat_after_minutes) for q in added] == [(question, "miss", None)]


def test_an_occurrence_whose_pinned_release_lacks_its_item_is_neither_drilled_nor_shown(
    tmp_path: Path,
) -> None:
    hub = morning(tmp_path)
    identifier = stored_with_unknown_item(hub)
    assert hub.runtime.drill_occurrence(identifier) is None
    with pytest.raises(ConfigInvalid) as error:
        hub.queries.occurrence(session(), identifier)
    assert error.value.findings[0].pointer == "/item_id"


def test_an_assistant_recheck_whose_window_already_closed_is_not_today(tmp_path: Path) -> None:
    hub = morning(tmp_path)
    hub.advance(datetime(2026, 11, 1, 20, tzinfo=UTC))
    with pytest.raises(RecheckRejected) as error:
        add_recheck(hub, "insight_late", due_from=datetime(2026, 11, 1, 8, tzinfo=UTC))
    assert error.value.code == "not_today"
    assert hub.rows("SELECT COUNT(*) FROM routine_occurrence WHERE cause_kind='assistant'") == ((0,),)


def test_history_reads_every_page_and_override_reads_refuse_past_their_bound(tmp_path: Path) -> None:
    hub = morning(tmp_path)
    hub.raise_warning()
    hub.deliver()
    full = hub.queries.occurrences(session(), "today", None, None, 200)
    assert len(cast(list[object], hub.queries.overrides(officer(), None, 200)["items"])) >= 2
    tight(hub, page_limit=1)
    assert hub.queries.occurrences(session(), "today", None, None, 200)["items"] == full["items"]
    tight(hub, page_limit=1, max_active_overrides_per_person=1, max_roster=1)
    with pytest.raises(ConfigInvalid) as error:
        hub.queries.overrides(officer(), None, 200)
    assert error.value.findings[0].pointer == "/overrides"


def test_an_override_awaiting_disposition_can_be_ended_by_the_medical_officer(tmp_path: Path) -> None:
    hub = morning(tmp_path)
    hub.raise_warning()
    hub.deliver()

    def vision() -> dict[str, object]:
        items = cast(list[dict[str, object]], hub.queries.overrides(officer(), None, 200)["items"])
        return next(o for o in items if o["item_id"] == "vision_self_check")

    assert vision()["allowed_actions"] == []
    hub.advance(hub.clock.now() + timedelta(minutes=5))
    hub.clear_warning(reason="config_changed")
    hub.deliver()
    assert vision()["state"] == "active" and vision()["allowed_actions"] == ["end"]
    crew = cast(list[dict[str, object]], hub.queries.overrides(session(), None, 200)["items"])
    assert next(o for o in crew if o["item_id"] == "vision_self_check")["allowed_actions"] == []


def test_tasks_need_mission_facts(tmp_path: Path) -> None:
    hub = morning(tmp_path)
    assert hub.queries.tasks(session(), None, 200)["items"]
    hub.mission.facts = None
    with pytest.raises(MissionUnavailable) as error:
        hub.queries.tasks(session(), None, 200)
    assert error.value.reason == "mission_facts_required"
