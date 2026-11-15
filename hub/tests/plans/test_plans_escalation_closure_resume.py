"""SYSTEM 8.1: old receipts retain closed history after a joined-help crash."""

from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.help.application.ports import HelpSessionSnapshot
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.uow import UnitOfWork
from crew_health.plans.application.act import EscalationResumer
from crew_health.plans.domain.run import PlanRun

from .plans_stack import HelpSessions, obj, rows, stack
from .test_plans_reconciliation_failures import event_for


@pytest.mark.trace("T28-01", "SYSTEM-8.1")
@pytest.mark.parametrize(
    ("closed", "prelinked", "closure_delivered"),
    [
        (True, False, False),
        (False, False, False),
        (True, True, False),
        (False, True, False),
        (True, True, True),
    ],
)
def test_joined_help_receipt_reconciles_authoritative_closure_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, closed: bool, prelinked: bool, closure_delivered: bool
) -> None:
    s = stack(tmp_path)
    run = s.start()
    saved = s.world.repository.save

    def fail_record(uow: UnitOfWork, run: PlanRun) -> None:
        raise StorageUnavailable("io_error")

    monkeypatch.setattr(s.world.repository, "save", fail_record)
    with pytest.raises(StorageUnavailable):
        s.act(run, "escalate", step_id="icl10_worse_chest", key="joined_crash")
    monkeypatch.setattr(s.world.repository, "save", saved)
    request = s.help_entry.calls[0]
    receipt = s.help_entry.receipts[request.escalation_id]

    class CurrentHelp(HelpSessions):
        def snapshot(self, help_session_id: str) -> HelpSessionSnapshot | None:
            base = super().snapshot("hlp_test")
            assert base is not None and help_session_id == receipt.help_session_id
            return replace(
                base,
                help_session_id=help_session_id,
                open=not closed,
                closed_at=s.clock.now() if closed else None,
            )

    world = replace(s.world, help_sessions=CurrentHelp())
    if prelinked:
        with s.db.begin(actor=s.session.actor, correlation_id="cor_early_link") as uow:
            s.consumers.on_help_session_changed(
                uow,
                event_for(
                    s,
                    {
                        "help_session_id": receipt.help_session_id,
                        "state": "open",
                        "flow_id": request.flow_id,
                        "entry": {"kind": "plan_step", "ref_id": run["plan_run_id"]},
                    },
                ),
            )
    if closed and (not prelinked or closure_delivered):
        # E26's public entry remains the original button, so its closure event cannot find the pending run.
        with s.db.begin(actor=s.session.actor, correlation_id="cor_button_close") as uow:
            s.consumers.on_help_session_changed(
                uow,
                event_for(
                    s,
                    {
                        "help_session_id": receipt.help_session_id,
                        "state": "closed",
                        "entry": {"kind": "button", "ref_id": None},
                    },
                ),
            )
    assert s.query("SELECT count(*) FROM plans_run_escalation") == ((int(prelinked),),)
    with s.db.snapshot() as view:
        pending = world.intents.pending(view, request.parent_command)
    assert pending is not None
    result = EscalationResumer(world).resume(s.session, pending)
    assert result is not None and result.status == 200
    current = s.runs.get(s.session, str(run["plan_run_id"]))
    assert current["state"] == ("active" if closed else "escalated")
    escalations = rows(current["escalations"])
    assert len(escalations) == 1
    assert s.query("SELECT session_closed FROM plans_run_escalation") == ((int(closed),),)
    assert current["revision"] == 2 + int(prelinked and closed)
    assert len(s.help_entry.calls) == 1
    assert EscalationResumer(world).resume(s.session, pending) == result
    assert s.runs.get(s.session, str(run["plan_run_id"])) == current
    assert len(s.events) == current["revision"]
    assert obj(s.events[-1]["data"])["state"] == ("active" if closed else "escalated")


@pytest.mark.trace("T28-01", "SYSTEM-8.1", "AGENTS-5")
@pytest.mark.parametrize(
    "fault", ["missing_reader", "unknown_session", "wrong_session", "failed_reader", "storage"]
)
def test_receipt_without_authoritative_help_state_stays_pending_and_recovers(
    tmp_path: Path, fault: str
) -> None:
    from crew_health.kernel.errors import ErrorCode, ProblemError
    from crew_health.plans.application.act import ActOnPlanRun

    s = stack(tmp_path)
    run = s.start()

    class UnavailableState(HelpSessions):
        def snapshot(self, help_session_id: str) -> HelpSessionSnapshot | None:
            if fault == "storage":
                raise StorageUnavailable("io_error")
            if fault == "failed_reader":
                raise RuntimeError("help query unavailable")
            return super().snapshot("hlp_test") if fault == "wrong_session" else None

    world = replace(s.world, help_sessions=None if fault == "missing_reader" else UnavailableState())
    s.actions = ActOnPlanRun(world)
    with pytest.raises(ProblemError) as error:
        s.act(run, "escalate", step_id="icl10_worse_chest", key="authoritative_state")
    if fault == "storage":
        assert isinstance(error.value, StorageUnavailable)
    else:
        assert error.value.code is ErrorCode.INTERNAL_ERROR
    assert s.query("SELECT state FROM plans_escalation_intent") == (("pending",),)
    assert s.query("SELECT count(*) FROM plans_run_escalation") == ((0,),)
    assert s.runs.get(s.session, str(run["plan_run_id"])) == run
    s.actions = ActOnPlanRun(s.world)
    recovered = s.act(run, "escalate", step_id="icl10_worse_chest", key="authoritative_state")
    assert recovered["state"] == "escalated" and len(rows(recovered["escalations"])) == 1
    assert len(s.help_entry.calls) == 1
