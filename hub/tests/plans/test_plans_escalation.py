"""SYSTEM 8.1 durable escalation intent and reconciliation fault cases."""

from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    EventScope,
    StoredEvent,
    UnitOfWork,
)
from crew_health.plans.application.act import EscalationResumer, PlanEscalationIntents
from crew_health.plans.domain.run import PlanRun as DomainRun

from .plans_stack import NOW, rows, stack


@pytest.mark.trace("T28-01", "SYSTEM-8.1")
def test_missing_help_provider_keeps_intent_pending_and_same_key_reconciles(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    s.rebind(help_entry=None)
    with pytest.raises(ProblemError) as error:
        s.act(run, "escalate", step_id="icl10_worse_chest", key="escalation")
    assert error.value.code is ErrorCode.INTERNAL_ERROR
    assert error.value.status == 500 and error.value.retryable is True
    assert s.query("SELECT state FROM plans_escalation_intent") == (("pending",),)
    assert s.query("SELECT completed FROM platform_command_intent") == ((0,),)
    assert s.runs.get(s.session, str(run["plan_run_id"]))["revision"] == 1
    assert len(s.events) == 1
    s.rebind(help_entry=s.help_entry)
    escalated = s.act(run, "escalate", step_id="icl10_worse_chest", key="escalation")
    assert escalated["state"] == "escalated"
    assert len(rows(escalated["escalations"])) == 1
    assert s.query("SELECT state FROM plans_escalation_intent") == (("recorded",),)
    assert s.query("SELECT completed FROM platform_command_intent") == ((1,),)
    assert s.events[-1]["type"] == "plan_run.escalated"
    assert s.act(run, "escalate", step_id="icl10_worse_chest", key="escalation") == escalated
    assert len(s.help_entry.receipts) == 1
    assert len(s.events) == 2


@pytest.mark.trace("T28-01", "SYSTEM-8.1")
def test_crash_after_help_commit_reuses_escalation_identity_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = stack(tmp_path)
    run = s.start()
    repository = s.world.repository
    original = repository.save

    def crash_record(uow: UnitOfWork, run: DomainRun) -> None:
        raise StorageUnavailable("io_error")

    monkeypatch.setattr(repository, "save", crash_record)
    with pytest.raises(StorageUnavailable):
        s.act(run, "escalate", step_id="icl10_worse_chest", key="crash")
    assert len(s.help_entry.receipts) == 1
    assert s.query("SELECT state FROM plans_escalation_intent") == (("pending",),)
    assert s.query("SELECT count(*) FROM plans_run_escalation") == ((0,),)
    assert len(s.events) == 1
    first = s.help_entry.calls[0]
    monkeypatch.setattr(repository, "save", original)
    escalated = s.act(run, "escalate", step_id="icl10_worse_chest", key="crash")
    assert len(rows(escalated["escalations"])) == 1
    assert {call.escalation_id for call in s.help_entry.calls} == {first.escalation_id}
    assert {call.help_command.key for call in s.help_entry.calls} == {first.escalation_id}
    assert first.help_command.operation == "internal.plan_help_entry"
    assert first.parent_command.operation == "actOnPlanRun"
    assert len(s.help_entry.receipts) == 1
    assert len(s.events) == 2


@pytest.mark.trace("T28-01", "SYSTEM-8.1")
def test_intent_reader_and_registered_resumer_complete_pending_parent(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    s.help_entry.fail = True
    with pytest.raises(ProblemError):
        s.act(run, "escalate", step_id="icl10_worse_chest", key="resume")
    request = s.help_entry.calls[0]
    with s.db.snapshot() as snapshot:
        read = PlanEscalationIntents(s.world).read(snapshot, s.session, request.escalation_id)
        pending = s.world.intents.pending(snapshot, request.parent_command)
    assert read == request
    assert pending is not None
    assert pending.resume_handler == "plans.escalation"
    s.help_entry.fail = False
    result = EscalationResumer(s.world).resume(s.session, pending)
    assert result is not None and result.status == 200
    assert s.query("SELECT state FROM plans_run") == (("escalated",),)
    assert len(s.events) == 2


@pytest.mark.trace("T28-01", "SYSTEM-8.1")
def test_closed_help_event_rederives_run_and_closed_run_refuses_new_escalation(tmp_path: Path) -> None:
    import json

    s = stack(tmp_path)
    run = s.start()
    escalated = s.act(run, "escalate", step_id="icl10_worse_chest")
    assert escalated["state"] == "escalated"
    event = StoredEvent(
        500,
        "event_help_closed",
        EventType.HELP_SESSION_CHANGED,
        NOW,
        NOW,
        EventScope("person", "mara", "lunar_surface"),
        s.release.release_id,
        "cor_help_closed",
        json.dumps(
            {
                "help_session_id": "hlp_escalated",
                "state": "closed",
                "flow_id": None,
                "entry": {"kind": "plan_step", "ref_id": run["plan_run_id"]},
            }
        ).encode(),
        Audience("help.session_viewers", "hlp_escalated", AudienceProjectionKey("help", "session", 1), "1"),
    )
    with s.db.begin(actor=s.session.actor, correlation_id="cor_help_closed") as uow:
        s.consumers.on_help_session_changed(uow, event)
    current = s.runs.get(s.session, str(run["plan_run_id"]))
    assert current["state"] == "active"
    assert s.events[-1]["type"] == "plan_run.changed"
    closed = s.act(current, "close", disposition="other")
    with pytest.raises(ProblemError) as error:
        s.act(closed, "escalate", step_id="icl10_worse_chest", key="after_close")
    assert error.value.code is ErrorCode.INVALID_TRANSITION


@pytest.mark.trace("T28-17", "R28-18", "PR-I9", "SYSTEM-8.1")
def test_corrupt_pinned_hash_never_uses_replacement_release_escalation_flow(tmp_path: Path) -> None:
    import json

    from crew_health.contracts_gen.enums import ConfigName

    from .plans_stack import obj

    s = stack(tmp_path)
    run = s.start()
    with s.db.begin(actor=s.session.actor, correlation_id="cor_corrupt_pin") as uow:
        uow.connection.execute("UPDATE plans_parsed_plan SET sha256=?", ("0" * 64,))
    document = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    plan = rows(document["plans"])[0]
    chest = next(
        step
        for stage in rows(plan["stages"])
        for step in rows(stage.get("steps", []))
        if step["step_id"] == "icl10_worse_chest"
    )
    original_flow = str(chest["opens_flow_id"])
    chest["opens_flow_id"] = "eye_injury"
    s.replace_document(ConfigName.ONBOARD_PLANS, document)
    restricted = s.runs.get(s.session, str(run["plan_run_id"]))
    assert restricted["allowed_actions"] == ["escalate", "close"]
    with pytest.raises(ProblemError) as error:
        s.act(run, "step_done", step_id="icl10_note_episodes")
    assert error.value.code is ErrorCode.INVALID_TRANSITION
    escalated = s.act(run, "escalate", step_id="icl10_worse_chest", key="corrupt_pin_escalation")
    assert escalated["state"] == "escalated"
    assert s.help_entry.calls[-1].flow_id == original_flow
    assert s.help_entry.calls[-1].flow_id != chest["opens_flow_id"]


@pytest.mark.trace("SYSTEM-8.1", "AGENTS-3.2")
def test_pending_escalations_are_bounded_and_an_earlier_pending_command_still_resumes(tmp_path: Path) -> None:
    from dataclasses import replace

    from crew_health.plans.application.act import ActOnPlanRun

    s = stack(tmp_path)
    s.world = replace(s.world, settings=replace(s.world.settings, max_escalations_per_run=2))
    s.actions = ActOnPlanRun(s.world)
    run = s.start()
    s.rebind(help_entry=None)
    for key in ("pending_first", "pending_second"):
        with pytest.raises(ProblemError) as error:
            s.act(run, "escalate", step_id="icl10_worse_chest", key=key)
        assert error.value.code is ErrorCode.INTERNAL_ERROR
    with pytest.raises(ProblemError) as error:
        s.act(run, "escalate", step_id="icl10_worse_chest", key="pending_third")
    assert error.value.code is ErrorCode.INVALID_VALUE
    assert s.query("SELECT occurrence FROM plans_escalation_intent ORDER BY occurrence") == ((1,), (2,))
    assert s.query("SELECT count(*) FROM platform_command_intent WHERE completed=0") == ((2,),)
    assert s.query("SELECT revision FROM plans_run") == ((1,),)
    s.rebind(help_entry=s.help_entry)
    completed = s.act(run, "escalate", step_id="icl10_worse_chest", key="pending_first")
    assert completed["state"] == "escalated"
    assert s.help_entry.calls[-1].occurrence == 1
    assert s.query("SELECT state FROM plans_escalation_intent ORDER BY occurrence") == (
        ("recorded",),
        ("pending",),
    )
    assert s.query("SELECT count(*) FROM platform_command_intent WHERE completed=0") == ((1,),)


@pytest.mark.trace("SYSTEM-8.1", "T28-01")
def test_distinct_escalation_commands_joining_same_help_session_keep_unique_occurrences(
    tmp_path: Path,
) -> None:
    s = stack(tmp_path)
    run = s.start()
    for key in ("join_first", "join_second", "join_third"):
        run = s.act(run, "escalate", step_id="icl10_worse_chest", key=key)
    assert [call.occurrence for call in s.help_entry.calls] == [1, 2, 3]
    assert len({call.escalation_id for call in s.help_entry.calls}) == 3
    assert len(s.help_entry.receipts) == 3
    assert {receipt.help_session_id for receipt in s.help_entry.receipts.values()} == {"hlp_escalated"}
    assert s.query("SELECT occurrence,state FROM plans_escalation_intent ORDER BY occurrence") == (
        (1, "recorded"),
        (2, "recorded"),
        (3, "recorded"),
    )
    assert len(rows(run["escalations"])) == 1
    assert len(s.events) == 2
