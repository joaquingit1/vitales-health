"""Recovery and transactional reconciliation faults preserve safety and accepted records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName, EventType
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.uow import Audience, AudienceProjectionKey, EventScope, StoredEvent
from crew_health.plans.application.pinning import load_context
from crew_health.plans.application.ports import ObservationRef
from crew_health.plans.application.teach_copies import GuidedCheckCompleted, GuidedCheckFacts

from .plans_stack import NOW, PlansStack, obj, rows, run_step, stack
from .test_plans_act import scratch_supply_step


def event_for(s: PlansStack, body: object, kind: EventType = EventType.HELP_SESSION_CHANGED) -> StoredEvent:
    return StoredEvent(
        1,
        "event_reconcile",
        kind,
        NOW,
        NOW,
        EventScope("person", "mara", "lunar_surface"),
        s.release.release_id,
        "cor_reconcile",
        canonical_json(body),
        Audience("help.session_viewers", "hlp_reconcile", AudienceProjectionKey("help", "session", 1), "1"),
    )


@pytest.mark.trace("T28-17", "R28-18", "PR-I9")
@pytest.mark.parametrize("damage", ["missing", "wrong_hash", "bad_json", "wrong_plan"])
def test_unusable_primary_plan_remains_restricted_and_closes_from_verified_backup(
    tmp_path: Path, damage: str
) -> None:
    s = stack(tmp_path)
    run = s.start()
    with s.db.begin(actor=s.session.actor, correlation_id="cor_damage") as uow:
        if damage == "missing":
            uow.connection.execute("DELETE FROM plans_parsed_plan", ())
        elif damage == "wrong_hash":
            uow.connection.execute("UPDATE plans_parsed_plan SET sha256=?", ("0" * 64,))
        else:
            raw = "{" if damage == "bad_json" else json.dumps({"plan_id": "plan_wrong"})
            digest = hashlib.sha256(raw.encode()).hexdigest()
            uow.connection.execute("UPDATE plans_parsed_plan SET plan_json=?,sha256=?", (raw, digest))
    restricted = s.runs.get(s.session, str(run["plan_run_id"]))
    assert restricted["allowed_actions"] == ["escalate", "close"]
    closed = s.act(run, "close", disposition="other")
    assert closed["state"] == "closed"
    assert closed["rechecks"] == run["rechecks"]


@pytest.mark.trace("T28-17", "PR-I9", "AGENTS-5")
def test_loss_of_both_plan_copies_keeps_close_and_reports_unknown_recheck_content(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    with s.db.begin(actor=s.session.actor, correlation_id="cor_both_lost") as uow:
        uow.connection.execute("DELETE FROM plans_parsed_plan", ())
        uow.connection.execute("DELETE FROM plans_run_context", ())
    document = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    document["plans"] = [p for p in rows(document["plans"]) if p["plan_id"] != "plan_icl10"]
    s.replace_document(ConfigName.ONBOARD_PLANS, document)
    closed = s.act(run, "close", disposition="other")
    assert closed["state"] == "closed"
    copy = obj(json.loads(s.docs[ConfigName.UI_COPY].raw))
    assert all(r["due_text"] == obj(copy["designed_states"])["unknown"] for r in rows(closed["rechecks"]))
    assert all(row["state"] == "open" for row in rows(closed["steps"]))


@pytest.mark.trace("PR-I9", "AGENTS-5")
@pytest.mark.parametrize(
    "damage",
    [
        None,
        [],
        {},
        {"plan": {}, "supplies": "bad"},
        {"plan": {}, "supplies": [{"supply_id": 1, "unit": "g", "consumable": True}]},
        {"plan": {}, "supplies": [], "ui_copy": None, "month_days": 0},
    ],
)
def test_malformed_recovery_metadata_is_never_interpreted_as_valid_configuration(
    tmp_path: Path, damage: object
) -> None:
    s = stack(tmp_path)
    run = s.start()
    encoded = canonical_json(damage).decode()
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with s.db.begin(actor=s.session.actor, correlation_id="cor_bad_metadata") as uow:
        uow.connection.execute("UPDATE plans_run_context SET context_json=?,sha256=?", (encoded, digest))
    with s.db.snapshot() as view:
        assert (
            load_context(
                s.world.repository, view, str(run["plan_run_id"]), s.world.settings.max_document_bytes
            )
            is None
        )
    current = s.runs.get(s.session, str(run["plan_run_id"]))
    assert current["allowed_actions"] == ["escalate", "close"]


@pytest.mark.trace("T28-05", "PR-I7", "EI12")
@pytest.mark.parametrize("fault", ["below_zero", "storage", "reference_invalid", "restricted"])
def test_guided_reconciliation_rolls_back_only_known_semantic_refusal(tmp_path: Path, fault: str) -> None:
    s = stack(tmp_path)
    scratch_supply_step(s)
    document = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    plan = rows(document["plans"])[0]
    steps = [step for stage in rows(plan["stages"]) for step in rows(stage.get("steps", []))]
    done_step = next(step for step in steps if step["step_id"] == "icl10_note_episodes")
    guided_step = next(step for step in steps if step["step_id"] == "icl10_pulse_by_crewmate")
    guided_step["needs"] = done_step["needs"]
    s.replace_document(ConfigName.ONBOARD_PLANS, document)
    run = s.start()
    s.observations.records["obs_pulse"] = ObservationRef("obs_pulse", "mara", "ep_test", "SIM", "accepted")
    facts = GuidedCheckFacts(
        "check_fault",
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
        facts.guided_check_id, facts.plan_run_id, "icl10_pulse_by_crewmate", "agree", facts.observation_ids
    )
    if fault == "below_zero":
        s.supplies.refuse = True
    elif fault == "reference_invalid":
        s.observations.records.clear()
    elif fault == "restricted":
        document["plans"] = [p for p in rows(document["plans"]) if p["plan_id"] != "plan_icl10"]
        s.replace_document(ConfigName.ONBOARD_PLANS, document)
    else:

        class BrokenStock:
            def stock(self, environment_id: str) -> Mapping[str, object]:
                raise StorageUnavailable("io_error")

        # Raise from a real adapter boundary without replacing plan logic.
        def fail_stock(environment_id: str) -> Mapping[str, object]:
            return BrokenStock().stock(environment_id)

        from unittest.mock import patch

        with patch.object(s.supplies, "stock", side_effect=fail_stock):
            with pytest.raises(StorageUnavailable):
                with s.db.begin(actor=s.session.actor, correlation_id="cor_guided_storage") as uow:
                    uow.connection.execute(
                        "INSERT INTO plans_test_debit VALUES (?,?)", ("producer_marker", "none")
                    )
                    s.consumers.on_guided_check_completed(uow, event)
        assert s.query("SELECT count(*) FROM plans_test_debit") == ((0,),)
        return
    with s.db.begin(actor=s.session.actor, correlation_id="cor_guided_refused") as uow:
        uow.connection.execute("INSERT INTO plans_test_debit VALUES (?,?)", ("producer_marker", "none"))
        s.consumers.on_guided_check_completed(uow, event)
    assert s.query("SELECT id FROM plans_test_debit") == (("producer_marker",),)
    assert (
        run_step(s.runs.get(s.session, str(run["plan_run_id"])), "icl10_pulse_by_crewmate")["state"] == "open"
    )
    assert len(s.events) == 1


@pytest.mark.trace("SYSTEM-8.1", "EI5")
def test_unrelated_durable_events_and_guided_events_without_run_do_not_mutate_plan(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start()
    with s.db.begin(actor=s.session.actor, correlation_id="cor_unrelated") as uow:
        s.consumers.on_guided_check_completed(
            uow, GuidedCheckCompleted("check_unbound", None, None, "agree", ())
        )
        s.consumers.on_help_session_changed(
            uow,
            event_for(
                s,
                {
                    "help_session_id": "hlp_other",
                    "state": "open",
                    "entry": {"kind": "plan_step", "ref_id": "prun_missing"},
                },
            ),
        )
        s.consumers.on_help_session_changed(uow, event_for(s, {}))
        s.consumers.on_handoff_changed(
            uow,
            event_for(
                s,
                {"plan_run_id": "prun_missing", "handoff_id": "h_1", "state": "queued"},
                EventType.HANDOFF_CHANGED,
            ),
        )
    assert s.runs.get(s.session, str(run["plan_run_id"])) == run
    assert len(s.events) == 1


@pytest.mark.trace("SYSTEM-5.3", "SYSTEM-8.1", "AGENTS-5")
def test_pending_escalation_never_claims_a_client_timestamp_is_hub_corrected(tmp_path: Path) -> None:
    from crew_health.plans.application.act import PlanEscalationIntents

    s = stack(tmp_path)
    run = s.start()
    s.rebind(help_entry=None)
    with pytest.raises(ProblemError):
        s.act(run, "escalate", step_id="icl10_worse_chest", key="clock_provenance")
    escalation_id = str(s.query("SELECT escalation_id FROM plans_escalation_intent")[0][0])
    reader = PlanEscalationIntents(s.world)
    with s.db.snapshot() as view:
        first = reader.read(view, s.session, escalation_id)
    assert first.client_time.original_at == NOW
    assert first.client_time.basis == "source_raw"
    assert first.client_time.segment_id == "unknown"
    s.clock.epoch = "process_restarted"
    with s.db.snapshot() as view:
        resumed = reader.read(view, s.session, escalation_id)
    assert resumed.client_time == first.client_time
    assert resumed.help_command == first.help_command


@pytest.mark.trace("SYSTEM-8.1", "D9")
def test_provided_escalation_reader_resolves_the_real_intent_and_rechecks_owner(tmp_path: Path) -> None:
    from crew_health.plans.application.provided import PlansProvided

    from .plans_stack import session

    s = stack(tmp_path)
    run = s.start()
    s.rebind(help_entry=None)
    with pytest.raises(ProblemError):
        s.act(run, "escalate", step_id="icl10_worse_chest", key="provided_intent")
    escalation_id = str(s.query("SELECT escalation_id FROM plans_escalation_intent")[0][0])
    provided = PlansProvided(s.world)
    with s.db.snapshot() as view:
        request = provided.read(view, s.session, escalation_id)
        assert request.plan_run_id == run["plan_run_id"]
        assert request.parent_command.key == "provided_intent"
        with pytest.raises(ProblemError) as refused:
            provided.read(view, session("tomas"), escalation_id)
        assert refused.value.code.value == "UNAUTHORIZED_SCOPE"
