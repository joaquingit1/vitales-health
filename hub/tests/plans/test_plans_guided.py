"""Teach's real event class completes plans in the publishing platform transaction."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from crew_health.plans.application.ports import ObservationRef
from crew_health.plans.application.teach_copies import GuidedCheckFacts
from crew_health.teach.application.ports import GuidedCheckCompleted

from .plans_stack import run_step, stack


@pytest.mark.trace("T28-05", "PR-I7")
@pytest.mark.parametrize("outcome", ["agree", "no_reference"])
def test_teach_event_completes_with_checked_observation_in_same_transaction(
    tmp_path: Path,
    outcome: Literal["agree", "no_reference"],
) -> None:
    s = stack(tmp_path)
    run = s.start()
    s.bind_plan_subscriptions()
    facts = GuidedCheckFacts(
        "gchk_1",
        "pulse_check_wrist",
        str(run["plan_run_id"]),
        "mara",
        "tomas",
        "mara",
        "checked",
        outcome,
        "SIM",
        ("obs_pulse",),
    )
    s.observations.records["obs_pulse"] = ObservationRef("obs_pulse", "mara", "ep_test", "SIM", "accepted")
    s.guided.checks[facts.guided_check_id] = facts
    event = GuidedCheckCompleted(
        facts.guided_check_id, facts.plan_run_id, "icl10_pulse_by_crewmate", outcome, facts.observation_ids
    )
    with s.db.begin(actor=s.session.actor, correlation_id="cor_guided") as uow:
        uow.publish(event)
    current = s.runs.get(s.session, str(run["plan_run_id"]))
    step = run_step(current, "icl10_pulse_by_crewmate")
    assert step["state"] == "done"
    assert step["guided_check_id"] == "gchk_1"
    assert step["observation_ids"] == ["obs_pulse"]
    assert s.events[-1]["type"] == "plan_run.step_done"
    assert len(s.events) == 2
    with s.db.begin(actor=s.session.actor, correlation_id="cor_guided_repeat") as uow:
        uow.publish(event)
    assert len(s.events) == 2
    assert s.runs.get(s.session, str(run["plan_run_id"])) == current


@pytest.mark.trace("T28-05", "PR-I7")
@pytest.mark.parametrize(
    "mismatch", ["disagree", "another_run", "another_subject", "another_skill", "closed"]
)
def test_inapplicable_guided_event_leaves_step_open_and_publishing_transaction_commits(
    tmp_path: Path,
    mismatch: str,
) -> None:
    s = stack(tmp_path)
    run = s.start()
    s.bind_plan_subscriptions()
    facts = GuidedCheckFacts(
        "gchk_1",
        "pulse_check_wrist",
        str(run["plan_run_id"]),
        "mara",
        "tomas",
        "mara",
        "checked",
        "agree",
        "SIM",
        ("obs_pulse",),
    )
    outcome: Literal["agree", "disagree", "no_reference"] = "agree"
    if mismatch == "another_run":
        facts = replace(facts, plan_run_id="prun_other")
    elif mismatch == "another_subject":
        facts = replace(facts, subject_person_id="tomas")
    elif mismatch == "another_skill":
        facts = replace(facts, skill_id="another_skill")
    elif mismatch == "disagree":
        outcome = "disagree"
        facts = replace(facts, result=outcome)
    elif mismatch == "closed":
        run = s.act(run, "close", disposition="other")
    s.observations.records["obs_pulse"] = ObservationRef("obs_pulse", "mara", "ep_test", "SIM", "accepted")
    s.guided.checks[facts.guided_check_id] = facts
    before = len(s.events)
    event = GuidedCheckCompleted(
        "gchk_1", str(run["plan_run_id"]), "icl10_pulse_by_crewmate", outcome, ("obs_pulse",)
    )
    with s.db.begin(actor=s.session.actor, correlation_id="cor_guided_ignored") as uow:
        uow.connection.execute("INSERT INTO plans_test_debit VALUES (?,?)", ("publish_marker", "no_supply"))
        uow.publish(event)
    assert s.query("SELECT id FROM plans_test_debit") == (("publish_marker",),)
    assert (
        run_step(s.runs.get(s.session, str(run["plan_run_id"])), "icl10_pulse_by_crewmate")["state"] == "open"
    )
    assert len(s.events) == before


@pytest.mark.trace("PR-I7", "AGENTS-6")
def test_provider_problem_is_not_swallowed_by_guided_event_handler(tmp_path: Path) -> None:
    from collections.abc import Mapping, Sequence

    from crew_health.contracts_gen.enums import ErrorCode
    from crew_health.kernel.errors import ProblemError

    class FailingObservations:
        def get_many(self, observation_ids: Sequence[str]) -> Mapping[str, ObservationRef]:
            raise ProblemError(ErrorCode.INVALID_VALUE, "upstream invariant failed")

    s = stack(tmp_path)
    run = s.start()
    s.rebind(observations=FailingObservations())
    s.bind_plan_subscriptions()
    facts = GuidedCheckFacts(
        "gchk_1",
        "pulse_check_wrist",
        str(run["plan_run_id"]),
        "mara",
        "tomas",
        "mara",
        "checked",
        "agree",
        "SIM",
        ("obs_pulse",),
    )
    s.guided.checks[facts.guided_check_id] = facts
    event = GuidedCheckCompleted(
        "gchk_1", str(run["plan_run_id"]), "icl10_pulse_by_crewmate", "agree", ("obs_pulse",)
    )
    with pytest.raises(ProblemError, match="upstream invariant failed"):
        with s.db.begin(actor=s.session.actor, correlation_id="cor_guided_fail") as uow:
            uow.connection.execute(
                "INSERT INTO plans_test_debit VALUES (?,?)", ("publish_marker", "no_supply")
            )
            uow.publish(event)
    assert s.query("SELECT count(*) FROM plans_test_debit") == ((0,),)
    assert len(s.events) == 1


@pytest.mark.trace("EI5", "V95")
@pytest.mark.parametrize(
    "state",
    ["queued", "in_transit", "delivered", "receipt_recorded", "delivery_failed", "cancelled", "expired"],
)
def test_actual_handoff_states_link_once_and_drafts_do_not_link(tmp_path: Path, state: str) -> None:
    import json

    from crew_health.contracts_gen.enums import EventType
    from crew_health.kernel.uow import Audience, AudienceProjectionKey, EventScope, StoredEvent

    from .plans_stack import NOW

    s = stack(tmp_path)
    run = s.start()

    def publish(state: str, handoff_id: str = "handoff_test") -> None:
        event = StoredEvent(
            500,
            "event_handoff",
            EventType.HANDOFF_CHANGED,
            NOW,
            NOW,
            EventScope("person", "mara", "lunar_surface"),
            s.release.release_id,
            "cor_handoff",
            json.dumps(
                {"plan_run_id": run["plan_run_id"], "handoff_id": handoff_id, "state": state}
            ).encode(),
            Audience("handoff.viewers", handoff_id, AudienceProjectionKey("handoff", "view", 1), "1"),
        )
        with s.db.begin(actor=s.session.actor, correlation_id="cor_handoff") as uow:
            s.consumers.on_handoff_changed(uow, event)

    publish("draft")
    assert s.runs.get(s.session, str(run["plan_run_id"]))["handoff_id"] is None
    assert len(s.events) == 1
    publish(state)
    linked = s.runs.get(s.session, str(run["plan_run_id"]))
    assert linked["handoff_id"] == "handoff_test"
    assert linked["revision"] == 2
    assert s.events[-1]["type"] == "plan_run.changed"
    publish(state)
    publish(state, "handoff_other")
    assert s.runs.get(s.session, str(run["plan_run_id"])) == linked
    assert len(s.events) == 2
