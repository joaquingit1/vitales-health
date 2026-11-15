"""Adversarial command inputs and failed providers leave no accepted partial work."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.help.application.ports import HelpSessionSnapshot
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.plans.application.act import ActOnPlanRun
from crew_health.plans.application.ports import HelpEscalationReceipt, ObservationRef, PlanEscalation
from crew_health.plans.application.runs import PlanRuns
from crew_health.plans.application.teach_copies import GuidedCheckFacts

from .plans_stack import NOW, PlansStack, rows, stack
from .test_plans_act import scratch_supply_step


def create_body(**changes: object) -> dict[str, object]:
    return {"plan_id": "plan_icl10", "subject_person_id": "mara", "episode_id": "ep_test", **changes}


def action_body(**changes: object) -> dict[str, object]:
    return {
        "action": "step_done",
        "step_id": "icl10_note_episodes",
        "client_time": NOW.isoformat().replace("+00:00", "Z"),
        **changes,
    }


def assert_unchanged(s: PlansStack) -> None:
    assert s.query("SELECT revision FROM plans_run") == ((1,),)
    assert s.query("SELECT count(*) FROM platform_command_ledger") == ((1,),)
    assert len(s.events) == 1


@pytest.mark.trace("T28-12", "T28-29")
def test_start_rejects_configuration_mismatch_before_any_run_is_written(tmp_path: Path) -> None:
    s = stack(tmp_path)
    with pytest.raises(ProblemError) as error:
        s.runs.start(s.session, create_body(), "mismatch", "release_old")
    assert error.value.code is ErrorCode.CONFIG_VERSION_MISMATCH
    assert s.query("SELECT count(*) FROM plans_run") == ((0,),)
    assert s.events == []


@pytest.mark.trace("T28-12", "R28-22")
@pytest.mark.parametrize("episode,help_id", [(None, None), ("ep_test", "hlp_test")])
def test_start_requires_exactly_one_clinical_context(
    tmp_path: Path, episode: str | None, help_id: str | None
) -> None:
    s = stack(tmp_path)
    with pytest.raises(ProblemError) as error:
        s.runs.start(s.session, create_body(episode_id=episode, help_session_id=help_id), "context")
    assert error.value.code is ErrorCode.INVALID_VALUE
    assert error.value.field_errors[0].field == "/episode_id"
    assert s.query("SELECT count(*) FROM plans_run") == ((0,),)


@pytest.mark.trace("T28-12", "R28-22")
@pytest.mark.parametrize("failure", ["absent", "unknown", "closed", "wrong_subject"])
def test_help_session_must_be_verifiably_open_for_the_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    s = stack(tmp_path)
    if failure == "absent":
        s.runs = PlanRuns(replace(s.world, help_sessions=None))
    elif failure == "unknown":
        s.help_sessions.subjects.clear()
    else:
        original = s.help_sessions.snapshot("hlp_test")
        assert original is not None
        altered = (
            replace(original, open=False)
            if failure == "closed"
            else replace(original, subject_person_id="tomas")
        )

        def snapshot(help_session_id: str) -> HelpSessionSnapshot | None:
            return altered

        monkeypatch.setattr(s.help_sessions, "snapshot", snapshot)
    with pytest.raises(ProblemError) as error:
        s.start(episode_id=None, help_session_id="hlp_test")
    assert error.value.code is ErrorCode.INVALID_VALUE
    assert error.value.field_errors[0].field == "/help_session_id"
    assert s.query("SELECT count(*) FROM plans_run") == ((0,),)
    assert s.events == []


@pytest.mark.trace("T28-12", "T28-08")
@pytest.mark.parametrize("failure", ["unknown_plan", "wrong_environment"])
def test_start_rejects_unknown_plan_or_environment_outside_plan(tmp_path: Path, failure: str) -> None:
    s = stack(tmp_path)
    plan_id = "plan_missing" if failure == "unknown_plan" else "plan_icl10"
    if failure == "wrong_environment":
        s.mission.environment = "not_in_plan"
    with pytest.raises(ProblemError) as error:
        s.start(plan_id=plan_id)
    assert error.value.code is (
        ErrorCode.INVALID_VALUE if failure == "unknown_plan" else ErrorCode.CONTEXT_INAPPLICABLE
    )
    assert s.events == []


@pytest.mark.trace("T28-29", "D9")
def test_missing_run_is_not_found_and_ground_get_requires_consent(tmp_path: Path) -> None:
    from crew_health.contracts_gen.enums import Role
    from crew_health.kernel.uow import Actor, SessionContext

    s = stack(tmp_path)
    with pytest.raises(ProblemError) as error:
        s.runs.get(s.session, "prun_missing")
    assert error.value.code is ErrorCode.NOT_FOUND
    ground = SessionContext(
        "ses_ground", "ground", Actor("ground", "ground_test"), None, frozenset({Role.GROUND})
    )
    with pytest.raises(ProblemError) as error:
        s.runs.get(ground, "prun_missing")
    assert error.value.code is ErrorCode.CONSENT_REQUIRED


@pytest.mark.trace("T28-12", "D9")
@pytest.mark.parametrize("limit", [0, -1, 201])
def test_list_refuses_out_of_bound_page_sizes(tmp_path: Path, limit: int) -> None:
    s = stack(tmp_path)
    with pytest.raises(ProblemError) as error:
        s.runs.list(s.session, limit=limit)
    assert error.value.code is ErrorCode.INVALID_VALUE
    assert error.value.field_errors[0].field == "/limit"
    assert s.runs.list(s.session)["items"] == []


@pytest.mark.trace("T28-13", "T28-29")
@pytest.mark.parametrize(
    "changes,pointer",
    [
        ({"step_id": None}, "/step_id"),
        ({"action": "not_available", "reason": None}, "/reason"),
        ({"action": "close", "disposition": None}, "/disposition"),
    ],
)
def test_action_requires_its_step_reason_or_disposition(
    tmp_path: Path,
    changes: dict[str, object],
    pointer: str,
) -> None:
    s = stack(tmp_path)
    run = s.start()
    with pytest.raises(ProblemError) as error:
        s.actions.act(s.session, str(run["plan_run_id"]), action_body(**changes), "invalid_action", 1)
    assert error.value.code is ErrorCode.INVALID_VALUE
    assert error.value.field_errors[0].field == pointer
    assert_unchanged(s)


@pytest.mark.trace("T28-13", "AGENTS-3.2")
@pytest.mark.parametrize("field", ["reason", "note", "observation_ids"])
def test_action_refuses_text_and_observation_inputs_over_hub_bounds(tmp_path: Path, field: str) -> None:
    s = stack(tmp_path)
    run = s.start()
    value: object = (
        [f"obs_{i}" for i in range(s.world.settings.max_observations_per_record + 1)]
        if field == "observation_ids"
        else "x" * (s.world.settings.max_text_chars + 1)
    )
    with pytest.raises(ProblemError) as error:
        s.actions.act(s.session, str(run["plan_run_id"]), action_body(**{field: value}), "oversized", 1)
    assert error.value.code is ErrorCode.INVALID_VALUE
    assert error.value.field_errors[0].field == "/" + field
    assert_unchanged(s)


@pytest.mark.trace("T28-13", "T28-29")
@pytest.mark.parametrize("action", ["step_done", "escalate"])
def test_action_configuration_mismatch_is_refused_for_both_transaction_paths(
    tmp_path: Path, action: str
) -> None:
    s = stack(tmp_path)
    run = s.start()
    step = "icl10_worse_chest" if action == "escalate" else "icl10_note_episodes"
    with pytest.raises(ProblemError) as error:
        s.actions.act(
            s.session,
            str(run["plan_run_id"]),
            action_body(action=action, step_id=step),
            "config_mismatch",
            1,
            "release_old",
        )
    assert error.value.code is ErrorCode.CONFIG_VERSION_MISMATCH
    assert_unchanged(s)
    assert s.query("SELECT count(*) FROM plans_escalation_intent") == ((0,),)


@pytest.mark.trace("R28-22", "T28-06")
@pytest.mark.parametrize(
    "failure",
    [
        "absent_reader",
        "unknown_id",
        "run",
        "subject",
        "measured_record",
        "skill",
        "state",
        "outcome",
        "origin",
        "observations",
        "unguided_step",
    ],
)
def test_guided_reference_is_checked_against_every_owner_fact(tmp_path: Path, failure: str) -> None:
    s = stack(tmp_path)
    run = s.start()
    facts = GuidedCheckFacts(
        "gchk_verified",
        "pulse_check_wrist",
        str(run["plan_run_id"]),
        "mara",
        "tomas",
        "mara",
        "checked",
        "agree",
        "SIM",
        ("obs_checked",),
    )
    match failure:
        case "run":
            facts = replace(facts, plan_run_id="prun_other")
        case "subject":
            facts = replace(facts, subject_person_id="tomas")
        case "measured_record":
            facts = replace(facts, measured_record_id="tomas")
        case "skill":
            facts = replace(facts, skill_id="other_skill")
        case "state":
            facts = replace(facts, state="open")
        case "outcome":
            facts = replace(facts, result="disagree")
        case "origin":
            facts = replace(facts, origin_mode="LIVE")
        case "observations":
            facts = replace(facts, observation_ids=("obs_other",))
    if failure == "absent_reader":
        s.actions = ActOnPlanRun(replace(s.world, guided_checks=None))
    elif failure != "unknown_id":
        s.guided.checks[facts.guided_check_id] = facts
    s.observations.records["obs_checked"] = ObservationRef(
        "obs_checked", "mara", "ep_test", "SIM", "accepted"
    )
    step = "icl10_note_episodes" if failure == "unguided_step" else "icl10_pulse_by_crewmate"
    with pytest.raises(ProblemError) as error:
        s.act(
            run,
            "step_done",
            step_id=step,
            guided_check_id=facts.guided_check_id,
            observation_ids=["obs_checked"],
        )
    assert error.value.code is ErrorCode.INVALID_VALUE
    assert error.value.field_errors[0].field == "/guided_check_id"
    assert_unchanged(s)


@pytest.mark.trace("T28-04", "R28-20")
def test_required_supply_ledger_absent_rolls_back_step_and_command(tmp_path: Path) -> None:
    s = stack(tmp_path)
    scratch_supply_step(s)
    run = s.start()
    s.actions = ActOnPlanRun(replace(s.world, supply_ledger=None))
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id="icl10_note_episodes")
    assert error.value.code is ErrorCode.INTERNAL_ERROR and error.value.retryable
    assert_unchanged(s)
    assert s.query("SELECT count(*) FROM plans_test_debit") == ((0,),)


@pytest.mark.trace("T28-01", "SYSTEM-8.1")
def test_help_runtime_failure_leaves_retryable_durable_intent(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    s.help_entry.fail = True
    with pytest.raises(ProblemError) as error:
        s.act(run, "escalate", step_id="icl10_worse_chest")
    assert error.value.code is ErrorCode.INTERNAL_ERROR and error.value.retryable
    assert s.query("SELECT state FROM plans_escalation_intent") == (("pending",),)
    assert_unchanged(s)


@pytest.mark.trace("T28-01", "SYSTEM-8.1")
def test_help_receipt_for_wrong_escalation_is_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = stack(tmp_path)
    run = s.start()
    original = s.help_entry.open_from_plan_step

    def wrong_receipt(request: PlanEscalation) -> HelpEscalationReceipt:
        return replace(original(request), escalation_id="pes_untrusted")

    monkeypatch.setattr(s.help_entry, "open_from_plan_step", wrong_receipt)
    with pytest.raises(ProblemError) as error:
        s.act(run, "escalate", step_id="icl10_worse_chest")
    assert error.value.code is ErrorCode.INTERNAL_ERROR and error.value.retryable
    assert s.query("SELECT state FROM plans_escalation_intent") == (("pending",),)
    assert_unchanged(s)


@pytest.mark.trace("T28-01", "SYSTEM-8.1", "AGENTS-6")
def test_help_storage_failure_propagates_without_losing_pending_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = stack(tmp_path)
    run = s.start()

    def fail_help(request: PlanEscalation) -> HelpEscalationReceipt:
        raise StorageUnavailable("io_error")

    monkeypatch.setattr(s.help_entry, "open_from_plan_step", fail_help)
    with pytest.raises(StorageUnavailable):
        s.act(run, "escalate", step_id="icl10_worse_chest")
    assert s.query("SELECT state FROM plans_escalation_intent") == (("pending",),)
    assert_unchanged(s)


@pytest.mark.trace("T28-01", "SYSTEM-8.1")
def test_run_closed_while_escalation_pending_keeps_later_help_receipt_as_history(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    s.rebind(help_entry=None)
    with pytest.raises(ProblemError):
        s.act(run, "escalate", step_id="icl10_worse_chest", key="pending_close")
    closed = s.act(run, "close", disposition="other")
    assert closed["state"] == "closed"
    s.rebind(help_entry=s.help_entry)
    reconciled = s.act(run, "escalate", step_id="icl10_worse_chest", key="pending_close")
    assert reconciled["state"] == "closed"
    assert reconciled["close"] == closed["close"]
    assert reconciled["revision"] == 3
    assert len(rows(reconciled["escalations"])) == 1
    assert s.query("SELECT state FROM plans_escalation_intent") == (("recorded",),)
    assert s.query("SELECT completed FROM platform_command_intent") == ((1,),)
    assert s.events[-1]["type"] == "plan_run.escalated"
