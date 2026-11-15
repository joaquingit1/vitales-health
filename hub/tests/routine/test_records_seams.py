"""Layer D uses real records and platform isolation, plus optional producer seams."""

import inspect
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.clock import NormalizedTime, TimeInput
from crew_health.kernel.evaluation import EvaluationInput, EvaluatorComputationFailure, EvaluatorRegistration
from crew_health.kernel.units import ConvertedQuantity, Quantity, Unit, WireQuantity
from crew_health.kernel.uow import Actor, SessionContext, UnitOfWork
from crew_health.platform.adapters.evaluation import EvaluationRepository
from crew_health.platform.application.evaluation import EvaluationService
from crew_health.records.domain.observations import ObservationRecord, QualityStatus
from crew_health.routine.application.evaluator import RoutineEvaluator
from crew_health.routine.application.ports import (
    DailyCheckAbandoned,
    DailyCheckSubmitted,
    OccurrenceActionBody,
    OverrideCreateBody,
    OverrideUntil,
    ReactionTestRecorded,
)
from crew_health.routine.domain.errors import NotFound
from crew_health.routine.domain.release import AddOccurrences, ObservationOn

from .harness import RoutineHarness, session, station
from .http_harness import HttpHarness, headers, problem
from .test_engine import drain
from .test_integration_inputs import at


def observation(
    hub: RoutineHarness,
    *,
    identifier: str = "obs_sleep",
    person: str = "person_mara",
    value: str = "5.5",
    unit: str = "h",
    quality: QualityStatus = "accepted",
) -> ObservationRecord:
    at = hub.clock.now()
    quantity = Quantity(Decimal(value), Unit(unit))
    observed = TimeInput(at, "clock_1", "segment_1", 1, "hub_corrected", "SIM")
    return ObservationRecord(
        identifier,
        person,
        person,
        "mission_1",
        None,
        "sleep_duration",
        quantity,
        ConvertedQuantity(WireQuantity(quantity.value, unit), quantity, "identity", "units_1", None, "exact"),
        observed,
        NormalizedTime(at, at, None, 0, "known"),
        at,
        at,
        "SIM",
        "self_report",
        quality,
        "quality_1",
        (),
        None,
        (),
        None,
        None,
        hub.release.release_id,
        None,
        None,
        "care_1",
    )


def evaluation(record: ObservationRecord) -> EvaluationInput:
    return EvaluationInput(
        "routine",
        "1",
        record.observation_id,
        record.observed,
        record.release_id,
        record.patient_id,
        1,
        "evaluation_" + record.observation_id,
    )


@pytest.mark.trace("D17", "T-E25-036", "INV-E25-16")
@pytest.mark.parametrize(
    ("value", "unit", "quality", "count", "mismatch"),
    [
        ("5.5", "h", "accepted", 1, 0),
        ("6", "h", "accepted", 0, 0),
        ("5.5", "min", "accepted", 0, 1),
        ("5.5", "h", "rejected", 0, 0),
    ],
)
@pytest.mark.trace("T-E25-034", "D17")
def test_real_observation_reaches_binding(
    hub_path: Path, value: str, unit: str, quality: QualityStatus, count: int, mismatch: int
) -> None:
    hub = station(hub_path)
    hub.advance(at(0, day=2))
    drain(hub)
    record = observation(hub, value=value, unit=unit, quality=quality)
    with hub.db.begin(actor=Actor("person", record.patient_id), correlation_id="cor_observation") as uow:
        hub.records.add_observation(uow, record)
        hub.runtime.evaluate(uow, evaluation(record))
    rows = hub.rows(
        "SELECT person_id,due_from FROM routine_occurrence WHERE item_id='reaction_test' AND periodic=0"
    )
    assert len(rows) == count
    if rows:
        assert rows[0][0] == record.patient_id
        with hub.db.snapshot() as view:
            release = hub.engine.release(view)
        binding = next(b for b in release.bindings if isinstance(b.on, ObservationOn))
        assert binding.item_id == "reaction_test"
        assert isinstance(binding.effect, AddOccurrences)
        minute = binding.effect.times[0][1]
        assert str(rows[0][1]).endswith(f"{minute // 60:02d}:{minute % 60:02d}:00Z")
    assert hub.rows(
        "SELECT count(*) FROM platform_audit WHERE json_extract(envelope,'$.entry.event_type')='routine.binding_unit_mismatch'"
    ) == ((mismatch,),)


@pytest.fixture
def hub_path(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.trace("D17", "INV-E25-16")
def test_real_platform_isolates_only_calculation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = station(tmp_path)
    record = observation(hub)
    service = EvaluationService(
        (EvaluatorRegistration("routine", "1", hub.runtime),),
        hub.settings,
        hub.ids,
        repository=EvaluationRepository(),
        capture=lambda _: record.observed,
        fault_codes=frozenset({"test_calculation"}),
    )

    def fail(self: RoutineEvaluator, uow: UnitOfWork, value: EvaluationInput) -> None:
        uow.connection.execute("INSERT INTO routine_clock_state VALUES (1,?)", ("2026-11-01T00:00:00Z",))
        raise EvaluatorComputationFailure("test_calculation")

    monkeypatch.setattr(RoutineEvaluator, "evaluate", fail)
    with hub.db.begin(actor=Actor("person", record.patient_id), correlation_id="cor_fault") as uow:
        hub.records.add_observation(uow, record)
        assert service.run(uow, evaluation(record)) == "pending"
    with hub.db.snapshot() as view:
        assert hub.records.observation(view, record.observation_id) == record
        assert service.pending(view, after_id=None, limit=10)[0].input.input_ref == record.observation_id
    assert hub.rows("SELECT count(*) FROM routine_clock_state") == ((0,),)
    assert hub.rows(
        "SELECT count(*) FROM platform_schedule WHERE json_extract(job,'$.handler')='platform.evaluation.retry'"
    ) == ((1,),)


@pytest.mark.trace("T-E25-018", "INV-E25-07")
@pytest.mark.trace("T-E25-028", "INV-09", "H9")
def test_linked_records_authorize_person_without_existence_oracle(tmp_path: Path) -> None:
    hub = HttpHarness(tmp_path)
    for record in (observation(hub), observation(hub, identifier="obs_other", person="person_tomas")):
        with hub.db.begin(
            actor=Actor("person", record.patient_id), correlation_id=record.observation_id
        ) as uow:
            hub.records.add_observation(uow, record)
    shown = hub.occurrence()
    path = next(
        r.path for r in hub.router.routes if isinstance(r, APIRoute) and r.operation_id == "actOnOccurrence"
    ).replace("{occurrence_id}", str(shown["occurrence_id"]))
    bodies = []
    for index, identifier in enumerate(("obs_other", "obs_unknown")):
        response = hub.client.post(
            path,
            json={"action": "complete", "observation_ids": [identifier]},
            headers=headers(key=f"linked_{index}", revision=1),
        )
        body = problem(response, "UNAUTHORIZED_SCOPE", 403)
        body.pop("correlation_id")
        bodies.append(body)
    assert bodies[0] == bodies[1]
    response = hub.client.post(
        path,
        json={"action": "complete", "observation_ids": ["obs_sleep"]},
        headers=headers(key="linked_own", revision=1),
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"]["observation_ids"] == ["obs_sleep"]


class Grants:
    def __init__(self, refs: frozenset[str]) -> None:
        self.refs = refs

    def can_read(self, session: SessionContext, object_ref: str) -> bool:
        return object_ref in self.refs


@pytest.mark.trace("T-E25-027", "INV-E25-07")
def test_person_override_care_requires_grant_and_ground_only_reads_own_start(tmp_path: Path) -> None:
    hub = station(tmp_path)
    owner = session()
    officer = session("person_tomas", roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
    ground = SessionContext("s_ground", "ground", Actor("ground", "ground_1"), None, frozenset({Role.GROUND}))
    _, private = hub.commands.create_override(
        session(roles=frozenset({Role.CREW_MEDICAL_OFFICER})),
        OverrideCreateBody("vision_self_check", 0, owner.person_id, OverrideUntil("person_stops")),
        idempotency_key="person_private",
    )
    ref = str(private["override_id"])
    assert hub.queries.overrides(officer, None, 100)["items"] == []
    assert hub.queries.overrides(ground, None, 100)["items"] == []
    with pytest.raises(NotFound):
        hub.runtime.end_override(officer, ref, if_match=1, idempotency_key="denied_private_end")
    hub.runtime.assemble(replace(hub.engine.deps, visibility=Grants(frozenset({ref}))))
    assert hub.runtime.overrides(officer, None, 100)["items"] == [private]
    assert hub.rows(
        "SELECT count(*) FROM platform_audit WHERE json_extract(envelope,'$.entry.event_type')='routine.care_read'"
    ) == ((1,),)
    assert hub.runtime.overrides(ground, None, 100)["items"] == []
    _, ground_private = hub.runtime.create_override(
        ground,
        OverrideCreateBody("vision_self_check", 0, "person_tomas", OverrideUntil("person_stops")),
        idempotency_key="ground_private",
        config_release=None,
    )
    assert hub.runtime.overrides(ground, None, 100)["items"] == [ground_private]


@pytest.mark.trace("T-E25-027", "INV-E25-07")
@pytest.mark.trace("T-E25-110", "3.14")
def test_occurrence_care_grant_controls_read_and_audit(tmp_path: Path) -> None:
    hub = station(tmp_path)
    ref = str(
        hub.rows("SELECT occurrence_id FROM routine_occurrence WHERE person_id='person_mara' LIMIT 1")[0][0]
    )
    officer = session("person_tomas", roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
    with pytest.raises(NotFound):
        hub.runtime.occurrence(officer, ref)
    hub.runtime.assemble(replace(hub.engine.deps, visibility=Grants(frozenset({ref}))))
    assert hub.runtime.occurrence(officer, ref)["person_id"] == "person_mara"
    assert hub.rows(
        "SELECT count(*) FROM platform_audit WHERE json_extract(envelope,'$.entry.event_type')='routine.care_read'"
    ) == ((1,),)


class Attachments:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def attach_routine_overrides(self, uow: UnitOfWork, warning_id: str, override_ids: Sequence[str]) -> None:
        assert uow.connection.rows(
            "SELECT count(*) FROM routine_override WHERE source_ref=?", (warning_id,), limit=1
        )[0][0] == len(override_ids)
        self.calls.append((warning_id, tuple(override_ids)))


@pytest.mark.trace("T-E25-031", "SYSTEM-8.6")
def test_warning_attachments_share_creating_and_repointing_transaction(tmp_path: Path) -> None:
    hub = station(tmp_path)
    attachments = Attachments()
    hub.runtime.assemble(replace(hub.engine.deps, warnings=attachments))
    hub.raise_warning()
    hub.deliver()
    initial = attachments.calls[-1]
    assert initial[0] == "wrn_co2_1" and initial[1]
    hub.clear_warning(reason="superseded")
    hub.raise_warning("wrn_co2_2", previous_warning_id="wrn_co2_1")
    hub.deliver()
    assert attachments.calls[-1] == ("wrn_co2_2", initial[1])


@pytest.mark.trace("T-E25-032", "T-E25-042")
@pytest.mark.trace("T-E25-099", "3.11")
def test_daily_and_reaction_notifications_do_not_change_question_instances(tmp_path: Path) -> None:
    hub = station(tmp_path)
    hub.raise_warning()
    hub.deliver()
    before = hub.rows("SELECT instance_id,state,answers_json FROM routine_question_instance")
    with hub.db.begin(actor=Actor("engine", "dailycheck"), correlation_id="daily") as uow:
        uow.publish(DailyCheckAbandoned("dc_1", "person_mara", 1))
    with hub.db.snapshot() as view:
        item = hub.engine.release(view).question_carrier_item_id()
        assert item is not None
        occ = hub.engine.deps.store.periodic_on_day(view, "person_mara", item, 1)
        assert occ is not None and occ.state != "done"
    at = occ.due_from
    assert at is not None
    hub.advance(at)
    with hub.db.begin(actor=Actor("engine", "dailycheck"), correlation_id="submitted") as uow:
        hub.engine.deps.store.put_barrier_ask(uow, "person_mara", 1, (item,), "pending", None, uow.now)
        uow.publish(DailyCheckSubmitted("dc_1", "person_mara", 1, "SIM", at))
    assert hub.rows("SELECT state FROM routine_occurrence WHERE occurrence_id=?", (occ.occurrence_id,)) == (
        ("done",),
    )
    assert hub.rows("SELECT outcome FROM routine_barrier_ask WHERE person_id='person_mara'") == (
        ("shown_unanswered",),
    )
    assert hub.rows("SELECT instance_id,state,answers_json FROM routine_question_instance") == before
    with hub.db.snapshot() as view:
        target = next(
            o
            for o in hub.engine.open_occurrences(view, "person_mara")
            if o.due_from is not None and o.due_from <= hub.clock.now()
        )
    for state, person in (("interrupted", "person_mara"), ("completed", "person_tomas")):
        with hub.db.begin(actor=Actor("engine", "dailycheck"), correlation_id="reaction_" + person) as uow:
            uow.publish(ReactionTestRecorded("rt_1", person, state, target.occurrence_id))
        assert hub.rows(
            "SELECT state FROM routine_occurrence WHERE occurrence_id=?", (target.occurrence_id,)
        ) == ((target.state,),)
    with hub.db.begin(actor=Actor("engine", "dailycheck"), correlation_id="reaction_complete") as uow:
        uow.publish(ReactionTestRecorded("rt_1", "person_mara", "completed", target.occurrence_id))
    assert hub.rows(
        "SELECT state FROM routine_occurrence WHERE occurrence_id=?", (target.occurrence_id,)
    ) == (("done",),)


@pytest.mark.trace("T-E25-032", "INV-E25-11")
def test_future_submission_leaves_occurrence_and_barrier_pending(tmp_path: Path) -> None:
    from datetime import timedelta

    hub = station(tmp_path)
    with hub.db.begin(actor=Actor("engine", "dailycheck"), correlation_id="future_submission") as uow:
        hub.engine.deps.store.put_barrier_ask(
            uow, "person_mara", 1, ("daily_check",), "pending", None, uow.now
        )
        uow.publish(DailyCheckSubmitted("dc_future", "person_mara", 1, "SIM", uow.now + timedelta(hours=1)))
    assert hub.rows("SELECT outcome FROM routine_barrier_ask") == (("pending",),)
    assert hub.rows("SELECT count(*) FROM routine_occurrence WHERE state='done'") == ((0,),)


@pytest.mark.trace("T-E25-027", "INV-E25-07")
@pytest.mark.trace("T-E25-039", "INV-09")
def test_event_audiences_recheck_care_grants_and_audit(tmp_path: Path) -> None:
    from typing import cast

    from crew_health.kernel.uow import AudienceRule

    from .test_faults import audience

    hub = station(tmp_path)
    hub.overlay(
        "ovl_private",
        kind="eva",
        state="ended",
        revision=2,
        started_at=hub.clock.now(),
        ended_at=hub.clock.now(),
    )
    hub.deliver()
    override = str(hub.rows("SELECT override_id FROM routine_override WHERE audience='person' LIMIT 1")[0][0])
    occurrence = str(
        hub.rows("SELECT occurrence_id FROM routine_occurrence WHERE person_id='person_mara' LIMIT 1")[0][0]
    )
    officer = session("person_tomas", roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
    ground = session(None, roles=frozenset({Role.GROUND}))
    for allowed in (False, True, False):
        hub.runtime.assemble(
            replace(
                hub.runtime.engine.deps,
                visibility=Grants(frozenset({override, occurrence}) if allowed else frozenset()),
            )
        )
        for kind, ref in (("routine.override", override), ("routine.occurrence", occurrence)):
            rule = cast(AudienceRule, hub.registry.declarations["audiences"][kind])
            with hub.db.snapshot() as view:
                assert rule.admits(view, officer, audience(kind, ref)) == allowed
                assert not rule.admits(view, ground, audience(kind, ref))
    assert hub.rows(
        "SELECT count(*) FROM platform_audit WHERE json_extract(envelope,'$.entry.event_type')='routine.care_read'"
    ) == ((2,),)


@pytest.mark.trace("T-E25-018", "INV-E25-07")
@pytest.mark.parametrize("invalid", ("mission", "origin", "quality"))
def test_linked_observation_rejects_foreign_partition_and_unaccepted_quality(
    tmp_path: Path, invalid: str
) -> None:
    from crew_health.routine.domain.errors import Unauthorized

    hub = station(tmp_path)
    record = observation(hub)
    if invalid == "mission":
        record = replace(record, mission_id="mission_foreign")
    elif invalid == "origin":
        record = replace(record, origin_mode="REPLAY")
        assert hub.mission.facts is not None
        hub.mission.facts = replace(hub.mission.facts, mission_mode="LIVE")
    else:
        record = replace(record, quality_status="rejected")
    with hub.db.begin(actor=Actor("person", record.patient_id), correlation_id="linked_partition") as uow:
        hub.records.add_observation(uow, record)
    identifier = str(
        hub.rows(
            "SELECT occurrence_id FROM routine_occurrence WHERE person_id='person_mara' AND state='due' LIMIT 1"
        )[0][0]
    )
    with pytest.raises(Unauthorized) as error:
        hub.runtime.act(
            session(),
            identifier,
            OccurrenceActionBody("complete", observation_ids=(record.observation_id,)),
            if_match=1,
            idempotency_key="invalid_link",
            config_release=None,
        )
    assert error.value.reason == "linked_record_scope"
    assert hub.rows("SELECT count(*) FROM platform_command_ledger") == ((0,),)


@pytest.mark.trace("T-E25-049", "SYSTEM-5")
def test_records_repository_satisfies_the_routine_record_facts_port() -> None:
    from crew_health.records.adapters.repository import RecordRepository
    from crew_health.records.application.ports import RecordsStore
    from crew_health.routine.application.ports import RecordFacts

    port: RecordFacts = RecordRepository()
    assert port is not None
    for name in ("observation", "answer"):
        ours = inspect.signature(getattr(RecordFacts, name)).parameters
        theirs = inspect.signature(getattr(RecordsStore, name)).parameters
        assert list(ours) == list(theirs)
