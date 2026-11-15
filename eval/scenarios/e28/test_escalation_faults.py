"""SYSTEM 8.1 restart/resumer evidence in E05 fallback; E26 keyed entry is SIM."""

import json
from pathlib import Path
from typing import cast

import pytest
from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.commands import CommandResumer
from crew_health.kernel.uow import Audience, AudienceProjectionKey, EventScope, StoredEvent
from stack import NOW, Station, checked, refused, rows

pytestmark = pytest.mark.trace("SYSTEM-8.1", "T28-12", "T28-13", "T28-14")


def test_pending_help_intent_restarts_and_registered_resumer_finishes_once(tmp_path: Path) -> None:
    station = Station(tmp_path)
    run = station.start()
    station.help.fail = True
    error = refused(
        station.act(run, "escalate", step="icl10_worse_chest", key="recover_escalation"),
        500,
        "INTERNAL_ERROR",
    )
    assert error["retryable"] is True
    request = station.help.calls[0]
    with station.db.snapshot() as snapshot:
        pending = station.runtime.need().intents.pending(snapshot, request.parent_command)
    assert pending is not None
    assert pending.resume_handler == "plans.escalation"
    assert station.help.receipts == {}
    station.restart()
    station.help.fail = False
    resumer = cast(CommandResumer, station.registry.declarations["resumers"]["plans.escalation"])
    result = resumer.resume(station.base.session, pending)
    assert result is not None
    assert result.status == 200
    replay = checked(station.act(run, "escalate", step="icl10_worse_chest", key="recover_escalation"))
    assert len(rows(replay["escalations"])) == 1
    assert len(station.help.receipts) == 1
    assert len(station.query("plans_run_escalation")) == 1
    assert resumer.resume(station.base.session, pending) == result
    refused(
        station.act(run, "escalate", step="icl10_worse_faint", key="recover_escalation"),
        409,
        "DUPLICATE_COMMAND",
    )
    assert len(station.help.receipts) == 1


def test_closed_help_followed_by_escalation_is_new_occurrence(tmp_path: Path) -> None:
    station = Station(tmp_path)
    run = station.start()
    escalated = checked(station.act(run, "escalate", step="icl10_worse_chest", key="first_escalation"))
    first = station.help.calls[-1]
    event = StoredEvent(
        500,
        "event_help_closed",
        EventType.HELP_SESSION_CHANGED,
        NOW,
        NOW,
        EventScope("person", "mara", "lunar_surface"),
        "release_e28_shipped",
        "close_help",
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
    with station.db.begin(actor=station.base.session.actor, correlation_id="help_closed") as uow:
        station.runtime.help_event(uow, event)
    current = checked(station.client.get("/v1/plan-runs/" + str(run["plan_run_id"])))
    assert current["state"] != escalated["state"]
    again = checked(station.act(current, "escalate", step="icl10_worse_chest", key="second_escalation"))
    second = station.help.calls[-1]
    assert second.occurrence == first.occurrence + 1
    assert second.escalation_id != first.escalation_id
    assert len(rows(again["escalations"])) == 2
    assert len(station.help.receipts) == 2
