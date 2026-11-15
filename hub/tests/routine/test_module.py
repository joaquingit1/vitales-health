"""Routine declaration inventory, fail-closed startup and the records event seam."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from crew_health.app.registry import DeclarationRegistry, Resolver
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.evaluation import EvaluationInput, EvaluatorRegistration
from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
from crew_health.kernel.uow import UnitOfWork
from crew_health.routine.application.ports import (
    AssistantRoutinePort,
    DailyCheckQuestionSource,
    RoutineCommands,
    RoutineCompletionPort,
    RoutineQueries,
    RoutineTodayPort,
)
from crew_health.routine.application.settings import specs
from crew_health.routine.module import RoutineRuntime, register

from .harness import session, station


@pytest.mark.trace("T-E25-015", "SYSTEM-5")
def test_register_declares_all_runtime_seams() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=256)
    register(registry)
    assert set(registry.declarations["settings"]) == {spec.key for spec in specs()}
    assert registry.declarations["prefixes"] == {
        "ro": "routine_occurrence",
        "rov": "routine_override",
        "rq": "routine_question_instance",
        "rca": "routine_cadence_activation",
    }
    assert set(registry.declarations["loader_checks"]) == {"routine.release"}
    assert set(registry.declarations["audiences"]) == {"routine.occurrence", "routine.override"}
    assert set(registry.declarations["scheduler_handlers"]) == {
        "routine.occurrence",
        "routine.day",
        "routine.override",
        "routine.instance",
        "routine.activation",
    }
    assert set(registry.declarations["durable_subscribers"]) == {"routine.inputs"}
    assert set(registry.declarations["subscriptions"]) == {
        "routine.clock_regressed",
        "routine.clock_corrected",
        "routine.daily_submitted",
        "routine.daily_abandoned",
        "routine.reaction",
    }
    assert set(registry.providers) == {
        RoutineQueries,
        RoutineCommands,
        RoutineTodayPort,
        DailyCheckQuestionSource,
        RoutineCompletionPort,
        AssistantRoutinePort,
    }
    assert set(registry.bindings) == {"routine"}
    assert len(registry.declarations["routers"]) == 1


@pytest.mark.trace("T-E25-015", "INV-E25-16")
def test_unbound_facades_and_scheduler_fail_loudly() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=256)
    register(registry)
    runtime = cast(RoutineRuntime, registry.providers[RoutineQueries][0](cast(Resolver, None)))
    with pytest.raises(ProblemError) as error:
        runtime.schedule(session(), "today")
    assert error.value.code is ErrorCode.INTERNAL_ERROR
    assert error.value.retryable
    with pytest.raises(ProblemError):
        runtime.due_today("person_mara", datetime(2026, 11, 1, tzinfo=UTC))
    handler = registry.declarations["scheduler_handlers"]["routine.day"]
    with pytest.raises(ProblemError):
        cast(Callable[[UnitOfWork, ScheduledJob], None], handler)(
            cast(UnitOfWork, None),
            ScheduledJob(
                "routine.day.person_mara",
                "routine.day",
                AbsoluteDeadline(datetime(2026, 11, 1, tzinfo=UTC)),
                {"person_id": "person_mara"},
                None,
            ),
        )


@pytest.mark.trace("T-E25-015", "T-E25-096")
def test_real_module_resolves_and_materializes_at_startup(tmp_path: Path) -> None:
    hub = station(tmp_path)
    assert hub.engine is hub.runtime.engine
    assert hub.queries is hub.runtime.queries
    assert hub.commands is hub.runtime.commands
    assert int(str(hub.rows("SELECT COUNT(*) FROM routine_occurrence")[0][0])) > 0
    assert hub.rows("SELECT COUNT(*) FROM platform_schedule WHERE key LIKE 'routine.day.%'") == ((4,),)


@pytest.mark.trace("T-E25-015", "T-E25-096")
def test_records_uses_one_registered_evaluator_and_no_answer_subscription() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=256)
    register(registry)
    registration = registry.declarations["evaluators"][("routine", "1")]
    assert isinstance(registration, EvaluatorRegistration)
    assert registration.name == "routine" and registration.version == "1"
    assert "routine.answer" not in registry.declarations["subscriptions"]


@pytest.mark.trace("T-E25-015", "T-E25-043")
def test_answer_handler_accepts_records_real_event(tmp_path: Path) -> None:
    import json

    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.uow import Actor
    from crew_health.records.domain.answers import AnswerRecord

    hub = station(tmp_path)
    hub.raise_warning()
    hub.deliver()
    rows = hub.rows(
        "SELECT instance_id, question_ids_json FROM routine_question_instance WHERE person_id=? ORDER BY due_from,instance_id",
        ("person_mara",),
    )
    assert rows
    question_id = str(json.loads(str(rows[0][1]))[0])
    now = hub.clock.now()
    answer = AnswerRecord(
        "ans_real_1",
        question_id,
        "questionnaire_1",
        "declined",
        None,
        None,
        None,
        Actor("person", "person_mara"),
        now,
        "SIM",
        None,
        "person_mara",
        "mission_1",
        None,
        hub.release.release_id,
        TimeInput(now, "clock_1", "segment_1", 1, "hub_corrected", "SIM"),
        now,
        None,
    )
    with hub.db.begin(actor=answer.actor, correlation_id="cor_answer_real") as uow:
        hub.records.add_answer(uow, answer)
        hub.runtime.evaluate(
            uow,
            EvaluationInput(
                "routine",
                "1",
                answer.answer_id,
                answer.observed,
                answer.release_id,
                answer.patient_id,
                1,
                "eval_real_answer",
            ),
        )
    rows = hub.rows(
        "SELECT answers_json FROM routine_question_instance WHERE person_id=? AND instance_id=?",
        ("person_mara", rows[0][0]),
    )
    assert json.loads(str(rows[0][0]))[question_id] == "ans_real_1"
