"""Controller-scoped tool contracts over the real plans database and ledger."""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from pathlib import Path
from typing import Literal, cast, get_type_hints

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.guidance.application.ports import Fact, OperatorFact, SkillFacts
from crew_health.help.application.ports import HelpSessionSnapshot
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.plans.application.assistant_copies import (
    AnswerScope,
    ApprovedPassages,
    Deadline,
    HandlerNoResult,
    HandlerOk,
    HandlerOutcome,
    HandlerRejected,
    HandlerUnavailable,
    ToolHandler,
    ToolRegistry,
    ToolScope,
)
from crew_health.plans.application.tools import PlanTools
from crew_health.plans.application.views import PlanViews
from crew_health.plans.application.world import PlansWorld
from crew_health.plans.domain.model import all_steps
from crew_health.plans.domain.text_check import find_amounts

from .http_harness import HttpStack
from .plans_stack import NOW, ROOT, HelpSessions, PlansStack, load_json, obj, rows, session, stack


class Answers:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []

    def answer_belongs(
        self, uow: UnitOfWork, answer_id: str, episode_id: str | None, help_session_id: str | None
    ) -> bool:
        self.calls.append((answer_id, episode_id, help_session_id))
        return answer_id == "answer_here" and (episode_id, help_session_id) in (
            ("ep_test", None),
            (None, "hlp_test"),
        )


class Passages:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.result: list[Mapping[str, JsonValue]] = [{"passage_id": "approved_reference"}]

    def passages(
        self, uow: UnitOfWork, release_id: str, source_ids: Sequence[str]
    ) -> Sequence[Mapping[str, JsonValue]]:
        self.calls.append((release_id, tuple(source_ids)))
        return self.result


class Registry:
    def __init__(self) -> None:
        self.handlers: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        assert name not in self.handlers
        self.handlers[name] = handler

    def handler(self, name: str) -> ToolHandler | None:
        return self.handlers.get(name)


def scope_for(s: PlansStack, run_id: str | None = None) -> ToolScope:
    return ToolScope(s.session, "mara", "ep_test", None, run_id, s.release.release_id, "turn_tools")


def invoke(
    s: PlansStack, tools: PlanTools, name: str, arguments: Mapping[str, JsonValue], scope: ToolScope
) -> HandlerOutcome:
    handler = cast(ToolHandler, getattr(tools, name))
    with s.db.begin(actor=s.session.actor, correlation_id="cor_tools") as uow:
        return handler.handle(
            uow,
            scope,
            arguments,
            Deadline(
                TimeInput(s.clock.now(), "hub", "segment", 0, "hub_corrected", "SIM"), timedelta(seconds=1)
            ),
        )


def database_contents(s: PlansStack) -> dict[str, object]:
    names = s.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    # Compare every table's rows as well as their counts, including the real ledger and audit.
    return {
        str(row[0]): (len(data), data)
        for row in names
        if (data := s.query('SELECT * FROM "' + str(row[0]).replace('"', '""') + '"')) is not None
    }


def choice(**updates: JsonValue) -> dict[str, JsonValue]:
    return {
        "plan_id": "plan_icl10",
        "step_id": "icl10_pulse_by_crewmate",
        "because_answer_id": "answer_here",
        **updates,
    }


@pytest.mark.trace("T28-42", "EI6")
def test_assistant_structural_copies_pin_fields_signatures_and_variants() -> None:
    assert tuple(f.name for f in fields(ToolScope)) == (
        "session",
        "subject_person_id",
        "episode_id",
        "help_session_id",
        "plan_run_id",
        "release_id",
        "turn_id",
    )
    assert get_type_hints(ToolScope) == {
        "session": SessionContext,
        "subject_person_id": str,
        "episode_id": str | None,
        "help_session_id": str | None,
        "plan_run_id": str | None,
        "release_id": str,
        "turn_id": str,
    }
    assert get_type_hints(HandlerOk) == {"result": Mapping[str, JsonValue]}
    assert get_type_hints(ToolHandler) == {"name": str, "effect": Literal["read", "compute", "proposal"]}
    for value, field in (
        (HandlerOk, "result"),
        (HandlerNoResult, "reason"),
        (HandlerUnavailable, "reason"),
        (HandlerRejected, "reason"),
    ):
        assert tuple(f.name for f in fields(value)) == (field,)
        assert vars(value)["__dataclass_params__"].frozen
        if field == "reason":
            assert get_type_hints(value) == {"reason": str}
    assert Deadline is ElapsedDeadline
    assert str(HandlerOutcome.__value__) == str(
        HandlerOk | HandlerNoResult | HandlerUnavailable | HandlerRejected
    )
    assert tuple(inspect.signature(ToolHandler.handle).parameters) == (
        "self",
        "uow",
        "scope",
        "arguments",
        "deadline",
    )
    assert get_type_hints(ToolHandler.handle) == {
        "uow": UnitOfWork,
        "scope": ToolScope,
        "arguments": Mapping[str, JsonValue],
        "deadline": Deadline,
        "return": HandlerOutcome,
    }
    assert get_type_hints(ToolRegistry.register) == {
        "name": str,
        "handler": ToolHandler,
        "return": type(None),
    }
    assert get_type_hints(ToolRegistry.handler) == {"name": str, "return": ToolHandler | None}
    assert get_type_hints(ApprovedPassages.passages) == {
        "uow": UnitOfWork,
        "release_id": str,
        "source_ids": Sequence[str],
        "return": Sequence[Mapping[str, JsonValue]],
    }
    assert get_type_hints(AnswerScope.answer_belongs) == {
        "uow": UnitOfWork,
        "answer_id": str,
        "episode_id": str | None,
        "help_session_id": str | None,
        "return": bool,
    }
    assert tuple(inspect.signature(ToolRegistry.register).parameters) == ("self", "name", "handler")
    assert tuple(inspect.signature(ToolRegistry.handler).parameters) == ("self", "name")
    assert tuple(inspect.signature(ApprovedPassages.passages).parameters) == (
        "self",
        "uow",
        "release_id",
        "source_ids",
    )
    assert tuple(inspect.signature(AnswerScope.answer_belongs).parameters) == (
        "self",
        "uow",
        "answer_id",
        "episode_id",
        "help_session_id",
    )
    frozen = ToolScope(session(), "mara", None, None, None, "release", "turn")
    with pytest.raises(FrozenInstanceError):
        frozen.__setattr__("subject_person_id", "tomas")


@pytest.mark.trace("T28-19", "V84", "EI6", "H28-3", "H28-9")
def test_ordered_proposal_guards_and_success_never_change_database_or_view(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    s = stack(tmp_path)
    run = s.start()
    scope = scope_for(s, str(run["plan_run_id"]))
    tools = PlanTools(s.world, answers=Answers())
    s.equipment.removed.add("camera_station")
    before = database_contents(s)
    view_before = PlanViews(s.world).plan(s.session, "plan_icl10", None)
    cases = (
        (choice(plan_id="plan_other", step_id="missing", note="give 5 mg"), "plan_not_active"),
        (choice(step_id="missing", note="give 5 mg"), "step_not_in_plan"),
        (choice(step_id="icl10_pulse_at_oximeter", note="give 5 mg"), "step_substitute_only"),
        (choice(step_id="icl10_pulse_at_cameras", note="give 5 mg"), "step_not_runnable"),
        (choice(because_answer_id="answer_other", note="give 5 mg"), "answer_out_of_scope"),
        (choice(note="give 5 mg"), "dose_in_text"),
    )
    with caplog.at_level(logging.WARNING, logger="crew_health.plans"):
        for arguments, reason in cases:
            assert invoke(s, tools, "choose_plan_step", arguments, scope) == HandlerRejected(reason)
            assert database_contents(s) == before
        assert invoke(s, tools, "choose_plan_step", choice(), scope) == HandlerOk(
            {"plan_id": "plan_icl10", "step_id": "icl10_pulse_by_crewmate", "proposed": True}
        )
    assert database_contents(s) == before
    assert PlanViews(s.world).plan(s.session, "plan_icl10", None) == view_before
    messages = [r.getMessage() for r in caplog.records if r.name == "crew_health.plans"]
    assert len(messages) == len(cases)
    for message, (_, reason) in zip(messages, cases, strict=True):
        assert message == f"choose_plan_step {scope.turn_id} {reason}"
    assert "give 5 mg" not in caplog.text and "answer_other" not in caplog.text


@pytest.mark.trace("T28-19", "EI6")
def test_get_plan_is_the_http_view_and_accepts_only_its_plan(tmp_path: Path) -> None:
    s = stack(tmp_path)
    scope = scope_for(s, str(s.start()["plan_run_id"]))
    tools = PlanTools(s.world)
    expected = PlanViews(s.world).plan(s.session, "plan_icl10", None)
    assert invoke(s, tools, "get_plan", {}, scope) == HandlerOk(cast(Mapping[str, JsonValue], expected))
    assert invoke(s, tools, "get_plan", {"plan_id": "plan_icl10"}, scope) == HandlerOk(
        cast(Mapping[str, JsonValue], expected)
    )
    assert invoke(s, tools, "get_plan", {"plan_id": "plan_other"}, scope) == HandlerRejected(
        "answer_out_of_scope"
    )


@pytest.mark.trace("T28-43", "EI6")
def test_explanation_is_release_text_with_only_provider_passages(tmp_path: Path) -> None:
    s = stack(tmp_path)
    scope = scope_for(s, str(s.start()["plan_run_id"]))
    passages = Passages()
    tools = PlanTools(s.world, passages=passages)
    arguments = {"plan_id": "plan_icl10", "step_id": "icl10_pulse_by_crewmate"}
    document = s.world.catalog.document(s.release)
    assert document is not None
    plan = document.plan("plan_icl10")
    assert plan is not None
    step = plan.step(arguments["step_id"])
    assert step is not None
    expected: dict[str, JsonValue] = {
        "text": step.text,
        "text_first_time": step.text_first_time,
        "source_ids": list(step.source_ids),
        "passages": [dict(passage) for passage in passages.result],
    }
    assert invoke(s, tools, "explain_step", arguments, scope) == HandlerOk(expected)
    assert passages.calls == [(scope.release_id, step.source_ids)]
    expected["passages"] = []
    assert invoke(s, PlanTools(s.world), "explain_step", arguments, scope) == HandlerOk(expected)
    assert invoke(s, tools, "explain_step", {**arguments, "step_id": "missing"}, scope) == HandlerRejected(
        "step_not_in_plan"
    )
    assert invoke(s, tools, "explain_step", {**arguments, "plan_id": "plan_other"}, scope) == HandlerRejected(
        "plan_not_active"
    )


@pytest.mark.trace("T28-19", "EI6", "H28-3")
def test_dose_policy_shared_detector_and_every_released_explanation(tmp_path: Path) -> None:
    assert find_amounts("take 2 tablets of 500 mg")
    s = stack(tmp_path)
    s.replace_document(ConfigName.ONBOARD_PLANS, load_json(ROOT / "config/onboard-plans.json"))
    tools = PlanTools(s.world)
    doc = s.world.catalog.document(s.release)
    assert doc is not None and doc.plans
    for plan in doc.plans:
        s.mission.environment = sorted(plan.environments)[0]
        run = s.start(key=plan.plan_id, plan_id=plan.plan_id)
        scope = scope_for(s, str(run["plan_run_id"]))
        for step in all_steps(plan):
            result = invoke(
                s, tools, "explain_step", {"plan_id": plan.plan_id, "step_id": step.step_id}, scope
            )
            assert isinstance(result, HandlerOk)
            assert not find_amounts(json.dumps(result.result))


@pytest.mark.trace("T28-19", "T28-30", "EI6")
@pytest.mark.parametrize(
    "name,arguments",
    [
        ("get_plan", {}),
        ("choose_plan_step", choice()),
        ("explain_step", {"plan_id": "plan_icl10", "step_id": "icl10_pulse_by_crewmate"}),
    ],
)
@pytest.mark.parametrize(
    "extra",
    [
        "subject_person_id",
        "episode_id",
        "help_session_id",
        "release_id",
        "plan_run_id",
        "subject",
        "episode",
        "run",
        "note_text",
    ],
)
def test_closed_arguments_reject_every_scope_override(
    tmp_path: Path, name: str, arguments: Mapping[str, JsonValue], extra: str
) -> None:
    s = stack(tmp_path)
    tools = PlanTools(s.world, answers=Answers())
    assert invoke(s, tools, name, {**arguments, extra: "private_canary"}, scope_for(s)) == HandlerRejected(
        "answer_out_of_scope"
    )


@pytest.mark.trace("T28-19", "EI6")
def test_missing_answer_provider_and_unknown_guidance_fail_closed(tmp_path: Path) -> None:
    s = stack(tmp_path)
    scope = scope_for(s, str(s.start()["plan_run_id"]))
    assert invoke(s, PlanTools(s.world), "choose_plan_step", choice(), scope) == HandlerRejected(
        "answer_out_of_scope"
    )
    s.content.unknown = True
    assert invoke(
        s, PlanTools(s.world, answers=Answers()), "choose_plan_step", choice(), scope
    ) == HandlerRejected("step_not_runnable")


@pytest.mark.trace("T28-19", "T28-43", "EI6")
def test_optional_registration_and_fresh_http_consumer(tmp_path: Path) -> None:
    registry = Registry()
    answers, passages = Answers(), Passages()
    h = HttpStack(
        tmp_path / "enabled",
        extra_ports=((ToolRegistry, registry), (AnswerScope, answers), (ApprovedPassages, passages)),
    )
    assert set(registry.handlers) == {"get_plan", "choose_plan_step", "explain_step"}
    assert {name: handler.effect for name, handler in registry.handlers.items()} == {
        "get_plan": "read",
        "choose_plan_step": "proposal",
        "explain_step": "read",
    }
    assert registry.handler("absent") is None
    assert registry.handler("get_plan") is h.runtime.tools.get_plan
    assert h.runtime.tools.answers is answers and h.runtime.tools.passages is passages
    run = h.start()
    scope = scope_for(h.stack, str(run["plan_run_id"]))
    result = invoke(h.stack, h.runtime.tools, "get_plan", {}, scope)
    assert isinstance(result, HandlerOk)
    assert result.result == h.client.get("/v1/plans/plan_icl10").json()
    for handler in registry.handlers.values():
        assert handler.name in registry.handlers
    assert h.client.get("/v1/plans/plan_icl10").status_code == 200
    off = HttpStack(tmp_path / "disabled")
    assert off.client.get("/v1/plans/plan_icl10").status_code == 200


@pytest.mark.trace("T28-19", "T28-30", "EI6")
def test_scope_identity_release_and_run_are_rechecked(tmp_path: Path) -> None:
    s = stack(tmp_path)
    scope = scope_for(s, str(s.start()["plan_run_id"]))
    tools = PlanTools(s.world)
    for forged in (
        replace(scope, subject_person_id="tomas"),
        replace(scope, episode_id="ep_other"),
        replace(scope, help_session_id="hlp_test"),
    ):
        with pytest.raises(ProblemError) as caught:
            invoke(s, tools, "get_plan", {}, forged)
        assert caught.value.code is ErrorCode.UNAUTHORIZED_SCOPE
    with pytest.raises(ProblemError) as caught:
        invoke(s, tools, "get_plan", {}, replace(scope, release_id="stale_release"))
    assert caught.value.code is ErrorCode.CONFIG_VERSION_MISMATCH


@pytest.mark.trace("T28-19", "T28-43", "EI6")
def test_unresolved_episode_and_rejected_plan_are_unavailable(tmp_path: Path) -> None:
    s = stack(tmp_path)
    tools = PlanTools(s.world, answers=Answers())
    scope = scope_for(s)
    assert invoke(s, tools, "get_plan", {}, scope) == HandlerNoResult("plan_not_active")
    assert invoke(s, tools, "choose_plan_step", choice(), scope) == HandlerRejected("plan_not_active")
    run_id = str(s.start()["plan_run_id"])
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    rows(raw["plans"])[0]["review_state"] = "rejected"
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)
    scope = scope_for(s, run_id)
    assert invoke(s, tools, "get_plan", {}, scope) == HandlerNoResult("plan_not_active")
    assert invoke(s, tools, "choose_plan_step", choice(), scope) == HandlerRejected("plan_not_active")
    assert invoke(
        s, tools, "explain_step", {"plan_id": "plan_icl10", "step_id": "missing"}, scope
    ) == HandlerRejected("plan_not_active")


@pytest.mark.trace("T28-19", "EI6", "H28-9")
def test_only_an_activated_substitute_is_proposable(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    scope = scope_for(s, str(run["plan_run_id"]))
    tools = PlanTools(s.world, answers=Answers())
    request = choice(step_id="icl10_pulse_at_oximeter")
    assert invoke(s, tools, "choose_plan_step", request, scope) == HandlerRejected("step_substitute_only")
    s.act(run, "use_substitute", step_id="icl10_pulse_at_cameras")
    before = database_contents(s)
    assert invoke(s, tools, "choose_plan_step", request, scope) == HandlerOk(
        {"plan_id": "plan_icl10", "step_id": "icl10_pulse_at_oximeter", "proposed": True}
    )
    assert database_contents(s) == before


@pytest.mark.trace("T28-19", "T28-43", "EI6", "T28-30")
def test_help_flow_without_a_run_uses_only_the_scoped_subject(tmp_path: Path) -> None:
    s = stack(tmp_path)
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    plan = rows(raw["plans"])[0]
    plan["scope"] = {"flow_id": "flow_palpitations"}
    plan["written_for"] = "flow"
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)

    class Sessions(HelpSessions):
        def snapshot(self, help_session_id: str) -> HelpSessionSnapshot:
            return HelpSessionSnapshot(
                help_session_id, 1, True, "mara", "mara", frozenset(), "flow_palpitations", "ui_test", None
            )

    s.world = replace(s.world, help_sessions=Sessions())
    tools = PlanTools(s.world, answers=Answers())
    scope = replace(scope_for(s), episode_id=None, help_session_id="hlp_test")
    view = invoke(s, tools, "get_plan", {}, scope)
    assert isinstance(view, HandlerOk) and view.result["plan_id"] == plan["plan_id"]
    assert invoke(s, tools, "choose_plan_step", choice(), scope) == HandlerOk(
        {"plan_id": "plan_icl10", "step_id": "icl10_pulse_by_crewmate", "proposed": True}
    )
    with pytest.raises(ProblemError) as caught:
        invoke(s, tools, "get_plan", {}, replace(scope, subject_person_id="tomas"))
    assert caught.value.code is ErrorCode.UNAUTHORIZED_SCOPE


@pytest.mark.trace("T28-43", "EI6", "AGENTS-1")
def test_configuration_edit_changes_explanation_without_code_change(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run_id = str(s.start()["plan_run_id"])
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    content = rows(rows(rows(raw["plans"])[0]["stages"])[0]["steps"])[0]
    updated_content: dict[str, JsonValue] = {
        "text": "Record the pulse check with its source.",
        "text_first_time": "Ask another crew member to check.",
        "source_ids": ["N18"],
    }
    content.update(updated_content)
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)
    result = invoke(
        s,
        PlanTools(s.world),
        "explain_step",
        {"plan_id": "plan_icl10", "step_id": str(content["step_id"])},
        scope_for(s, run_id),
    )
    assert result == HandlerOk({**updated_content, "passages": []})


@pytest.mark.trace("T28-19", "T28-43", "I2")
@pytest.mark.parametrize(
    "failure",
    [
        OSError("storage_canary"),
        ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "auth_canary"),
        ValueError("config_canary"),
    ],
)
def test_provider_errors_propagate_without_logging_private_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, failure: Exception
) -> None:
    s = stack(tmp_path)
    scope = scope_for(s, str(s.start()["plan_run_id"]))

    class BrokenAnswers:
        def answer_belongs(
            self, uow: UnitOfWork, answer_id: str, episode_id: str | None, help_session_id: str | None
        ) -> bool:
            raise failure

    with pytest.raises(type(failure)) as caught:
        invoke(s, PlanTools(s.world, answers=BrokenAnswers()), "choose_plan_step", choice(), scope)
    assert caught.value is failure
    assert "canary" not in caplog.text


@pytest.mark.trace("T28-19", "I2")
@pytest.mark.parametrize(
    "name,arguments",
    [
        ("get_plan", {"plan_id": None}),
        ("choose_plan_step", {"plan_id": "plan_icl10", "step_id": [], "because_answer_id": "answer_here"}),
        ("choose_plan_step", {"plan_id": "plan_icl10", "step_id": "x"}),
        ("explain_step", {"plan_id": "plan_icl10", "step_id": 3}),
    ],
)
def test_malformed_argument_shapes_fail_closed(
    tmp_path: Path, name: str, arguments: Mapping[str, JsonValue]
) -> None:
    s = stack(tmp_path)
    assert invoke(s, PlanTools(s.world), name, arguments, scope_for(s)) == HandlerRejected(
        "answer_out_of_scope"
    )


@pytest.mark.trace("T28-19", "I2", "EI6")
@given(
    extra=st.text(min_size=1).filter(
        lambda key: key not in {"plan_id", "step_id", "because_answer_id", "note"}
    )
)
def test_arbitrary_unknown_keys_are_rejected_before_reading_scope(extra: str) -> None:
    def unavailable() -> PlansWorld:
        raise AssertionError("invalid arguments reached the plans world")

    tools = PlanTools(unavailable)
    scope = ToolScope(session(), "mara", "ep_test", None, None, "release", "turn")
    deadline = Deadline(TimeInput(NOW, "hub", "segment", 0, "hub_corrected", "SIM"), timedelta(seconds=1))
    for handler, arguments in (
        (tools.get_plan, {}),
        (tools.choose_plan_step, choice()),
        (tools.explain_step, {"plan_id": "plan_icl10", "step_id": "icl10_pulse_by_crewmate"}),
    ):
        assert handler.handle(
            cast(UnitOfWork, object()), scope, {**arguments, extra: "private"}, deadline
        ) == HandlerRejected("answer_out_of_scope")


@pytest.mark.trace("T28-19", "T28-43", "EI6")
def test_help_run_cannot_supply_a_plan_for_an_unrelated_flow(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start(episode_id=None, help_session_id="hlp_test")
    scope = replace(scope_for(s, str(run["plan_run_id"])), episode_id=None, help_session_id="hlp_test")
    tools = PlanTools(s.world, answers=Answers())
    assert invoke(s, tools, "get_plan", {}, scope) == HandlerNoResult("plan_not_active")
    assert invoke(s, tools, "choose_plan_step", choice(), scope) == HandlerRejected("plan_not_active")


@pytest.mark.trace("T28-19", "T28-30", "EI6")
@pytest.mark.parametrize("missing", ["episode", "sessions", "permission", "snapshot"])
def test_missing_scope_facts_deny_access(tmp_path: Path, missing: str) -> None:
    s = stack(tmp_path)

    class Sessions(HelpSessions):
        def readable_by(self, caller: SessionContext, help_session_id: str) -> bool:
            return missing != "permission"

        def snapshot(self, help_session_id: str) -> HelpSessionSnapshot | None:
            return None if missing == "snapshot" else super().snapshot(help_session_id)

    scope = scope_for(s)
    if missing == "episode":
        s.rebind(episodes=None)
    else:
        s.world = replace(s.world, help_sessions=None if missing == "sessions" else Sessions())
        scope = replace(scope, episode_id=None, help_session_id="hlp_test")
    with pytest.raises(ProblemError) as caught:
        invoke(s, PlanTools(s.world), "get_plan", {}, scope)
    assert caught.value.code is ErrorCode.UNAUTHORIZED_SCOPE


@pytest.mark.trace("T28-19", "T28-30", "EI6")
def test_station_tool_view_uses_the_controllers_subject_for_eligibility(tmp_path: Path) -> None:
    from crew_health.contracts_gen.enums import Role
    from crew_health.kernel.uow import Actor

    s = stack(tmp_path)
    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    plan = rows(raw["plans"])[0]
    plan["scope"] = {"flow_id": "flow_palpitations"}
    plan["written_for"] = "flow"
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)
    seen: list[str | None] = []

    class Sessions(HelpSessions):
        def readable_by(self, caller: SessionContext, help_session_id: str) -> bool:
            return caller.kind == "station" and help_session_id == "hlp_test"

        def snapshot(self, help_session_id: str) -> HelpSessionSnapshot:
            snapshot = super().snapshot(help_session_id)
            assert snapshot is not None
            return replace(snapshot, flow_id="flow_palpitations")

    class Skills(SkillFacts):
        def operators(
            self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
        ) -> Fact[tuple[OperatorFact, ...]]:
            seen.append(subject_person_id)
            return s.crew.operators(snapshot, subject_person_id, patient_location_id)

    s.world = replace(s.world, help_sessions=Sessions(), skills=Skills())
    station = SessionContext(
        "ses_station", "station", Actor("station", "station"), None, frozenset({Role.STATION})
    )
    scope = replace(scope_for(s), session=station, episode_id=None, help_session_id="hlp_test")
    result = invoke(s, PlanTools(s.world), "get_plan", {}, scope)
    assert isinstance(result, HandlerOk) and result.result["plan_id"] == "plan_icl10"
    assert seen == [scope.subject_person_id]
