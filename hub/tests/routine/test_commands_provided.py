# Independent layer B review evidence: these tests drive the registered module through
# the real SQLite command ledger, scheduler, event validator and durable repositories.
# Privacy assertions inspect stored receipts and an altered current session grant.
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.routine.application.ports import (
    OccurrenceActionBody,
    OverrideCreateBody,
    OverrideUntil,
)
from crew_health.routine.domain.errors import (
    ActionNotAllowed,
    ConfigVersionMismatch,
    DuplicateOverride,
    InvalidBody,
    InvalidTransition,
    NotFound,
    RecheckRejected,
    StaleVersion,
    Unauthorized,
    VersionRequired,
)

from .harness import RoutineHarness, session, station


def occurrence_id(h: RoutineHarness, item: str, person: str = "person_mara") -> str:
    rows = h.rows(
        "SELECT occurrence_id FROM routine_occurrence WHERE person_id=? AND item_id=? AND periodic=1 ORDER BY mission_day DESC",
        (person, item),
    )
    assert rows
    return cast(str, rows[0][0])


def morning(tmp_path: Path, **values: object) -> RoutineHarness:
    return station(tmp_path, at=datetime(2026, 11, 1, 8, tzinfo=UTC), values=values)


def officer(person: str = "person_mara") -> SessionContext:
    return session(person, roles=frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER}))


def manual_body(*, days: int | None = None) -> OverrideCreateBody:
    return OverrideCreateBody(
        "vision_self_check",
        0,
        "person_mara",
        OverrideUntil("person_stops" if days is None else "days", days),
        "private manual note",
    )


@pytest.mark.trace("T-E25-002", "T-E25-022", "INV-E25-01", "INV-E25-07")
def test_command_ledger_replays_recorded_private_view_after_cache_lifetime(tmp_path: Path) -> None:
    h = morning(tmp_path)
    identifier = occurrence_id(h, "daily_check")
    body = OccurrenceActionBody("complete", note="private completion note")
    status, accepted = h.commands.act(session(), identifier, body, if_match=1, idempotency_key="cmd_complete")
    assert status == 200 and accepted["state"] == "done" and accepted["revision"] == 2
    before = h.rows("SELECT count(*) FROM routine_occurrence_transition WHERE occurrence_id=?", (identifier,))
    h.advance(h.clock.now() + timedelta(days=2))
    status, replay = h.commands.act(
        session(), identifier, body, if_match=None, idempotency_key="cmd_complete", config_release="rel_stale"
    )
    assert status == 200 and replay == accepted
    assert replay["revision"] == accepted["revision"] and replay["outcome"] == accepted["outcome"]
    assert (
        h.rows("SELECT count(*) FROM routine_occurrence_transition WHERE occurrence_id=?", (identifier,))
        == before
    )
    stored = h.rows(
        "SELECT safe_body,resource_ref,result_code FROM platform_command_ledger WHERE command_key=?",
        ("cmd_complete",),
    )
    assert stored == ((None, identifier, "accepted"),)
    assert h.rows("SELECT count(*) FROM platform_idempotency WHERE command_key=?", ("cmd_complete",)) == (
        (0,),
    )
    # Same actor and ledger identity, but the current crew-person grant changed.
    with pytest.raises(NotFound):
        h.commands.act(
            replace(session(), person_id="person_tomas"),
            identifier,
            body,
            if_match=None,
            idempotency_key="cmd_complete",
        )
    with pytest.raises(NotFound):
        h.queries.occurrence(session("person_tomas"), identifier)


@pytest.mark.trace("T-E25-002", "INV-E25-07")
def test_command_key_covers_body_and_target_and_rejected_requests_change_nothing(tmp_path: Path) -> None:
    h = morning(tmp_path)
    first, second = occurrence_id(h, "daily_check"), occurrence_id(h, "hydration")
    body = OccurrenceActionBody("complete")
    h.commands.act(session(), first, body, if_match=1, idempotency_key="cmd_same")
    for identifier, changed in ((first, replace(body, note="different")), (second, body)):
        with pytest.raises(ProblemError) as conflict:
            h.commands.act(session(), identifier, changed, if_match=1, idempotency_key="cmd_same")
        assert conflict.value.code == ErrorCode.DUPLICATE_COMMAND
    assert h.queries.occurrence(session(), second)["revision"] == 1
    assert h.rows("SELECT count(*) FROM platform_command_ledger WHERE command_key='cmd_same'") == ((1,),)


@pytest.mark.trace("T-E25-001", "T-E25-004", "INV-E25-07")
def test_command_versions_release_and_unverifiable_links_are_enforced(tmp_path: Path) -> None:
    h = morning(tmp_path)
    identifier = occurrence_id(h, "daily_check")
    body = OccurrenceActionBody("complete")
    with pytest.raises(Unauthorized):
        h.commands.act(session(None), identifier, body, if_match=1, idempotency_key="cmd_station")
    with pytest.raises(VersionRequired):
        h.commands.act(session(), identifier, body, if_match=None, idempotency_key="cmd_missing_version")
    with pytest.raises(StaleVersion):
        h.commands.act(session(), identifier, body, if_match=2, idempotency_key="cmd_stale")
    with pytest.raises(ConfigVersionMismatch):
        h.commands.act(
            session(), identifier, body, if_match=1, idempotency_key="cmd_config", config_release="rel_old"
        )
    for linked in (replace(body, observation_ids=("obs_1",)), replace(body, answer_ids=("ans_1",))):
        with pytest.raises(Unauthorized) as error:
            h.commands.act(session(), identifier, linked, if_match=1, idempotency_key="cmd_linked")
        assert error.value.reason == "linked_records_unverifiable"
    assert h.queries.occurrence(session(), identifier)["revision"] == 1
    assert h.rows("SELECT count(*) FROM platform_command_ledger") == ((0,),)


@pytest.mark.trace("T-E25-006", "T-E25-022", "INV-E25-13")
def test_manual_override_duplicate_end_role_and_ledger_replay(tmp_path: Path) -> None:
    h = morning(tmp_path)
    body = manual_body()
    with pytest.raises(Unauthorized):
        h.commands.create_override(session(), body, idempotency_key="cmd_crew")
    status, created = h.commands.create_override(officer(), body, idempotency_key="cmd_manual")
    identifier = cast(str, created["override_id"])
    assert status == 201 and created["started_by"] == "person"
    assert created["binding_id"] is None and created["audience"] == "person"
    assert h.commands.create_override(officer(), body, idempotency_key="cmd_manual") == (201, created)
    with pytest.raises(DuplicateOverride) as duplicate:
        h.commands.create_override(officer(), body, idempotency_key="cmd_manual_new")
    assert duplicate.value.override_id == identifier
    with pytest.raises(ActionNotAllowed):
        h.commands.end_override(
            session(roles=frozenset({Role.GROUND})), identifier, if_match=1, idempotency_key="cmd_ground_stop"
        )
    status, ended = h.commands.end_override(officer(), identifier, if_match=1, idempotency_key="cmd_end")
    assert status == 200 and ended["state"] == "ended" and ended["end_reason"] == "stopped_by_person"
    assert h.commands.end_override(officer(), identifier, if_match=None, idempotency_key="cmd_end") == (
        200,
        ended,
    )
    with pytest.raises(InvalidTransition):
        h.commands.end_override(officer(), identifier, if_match=2, idempotency_key="cmd_end_again")
    assert h.rows("SELECT count(*) FROM routine_override") == ((1,),)
    assert h.rows("SELECT count(*) FROM platform_command_ledger WHERE safe_body IS NOT NULL") == ((0,),)


@pytest.mark.trace("T-E25-006", "INV-E25-13")
def test_manual_override_validates_real_trigger_roster_end_and_release(tmp_path: Path) -> None:
    h = morning(tmp_path)
    body = manual_body()
    for bad in (
        replace(body, item_id="missing"),
        replace(body, trigger_index=1000),
        replace(body, trigger_index=1),
        replace(body, person_id="person_unknown"),
        replace(body, until=OverrideUntil("days")),
    ):
        with pytest.raises(InvalidBody):
            h.commands.create_override(officer(), bad, idempotency_key="cmd_invalid")
    with pytest.raises(ConfigVersionMismatch):
        h.commands.create_override(
            officer(), body, idempotency_key="cmd_bad_release", config_release="rel_other"
        )
    assert h.rows("SELECT count(*) FROM routine_override") == ((0,),)
    status, created = h.commands.create_override(officer(), manual_body(days=2), idempotency_key="cmd_days")
    assert status == 201
    ends = cast(dict[str, object], created["ends"])
    assert ends["kind"] == "days"
    assert ends["at"] == "2026-11-03T08:00:00Z"
    with pytest.raises(ActionNotAllowed):
        h.commands.end_override(
            officer(), cast(str, created["override_id"]), if_match=1, idempotency_key="cmd_days_stop"
        )


def add_recheck(
    h: RoutineHarness,
    insight: str,
    *,
    person: str = "person_mara",
    item: str = "vision_self_check",
    due_from: datetime | None = None,
) -> str:
    with h.db.begin(actor=Actor("assistant", "assistant_1"), correlation_id=h.ids.new("cor")) as uow:
        ref = h.runtime.add_recheck(
            uow, person_id=person, item_id=item, insight_id=insight, due_from=due_from or h.clock.now()
        )
        return ref.occurrence_id


@pytest.mark.trace("T-E25-010", "T-E25-094", "INV-E25-05")
def test_assistant_five_rejection_codes_and_nonperiodic_history(tmp_path: Path) -> None:
    h = morning(tmp_path, **{"routine.max_assistant_rechecks_per_person_day": 1})
    with h.db.snapshot() as view:
        before = h.engine.deps.store.latest_periodic_final_day(view, "person_mara", "vision_self_check")
    cases = (("item_unknown", {"item": "missing"}), ("person_mismatch", {"person": "person_absent"}))
    for expected, parameters in cases:
        with pytest.raises(RecheckRejected) as error:
            add_recheck(
                h,
                "insight_bad",
                item=parameters.get("item", "vision_self_check"),
                person=parameters.get("person", "person_mara"),
            )
        assert error.value.code == expected
    with pytest.raises(RecheckRejected) as error:
        add_recheck(h, "insight_old", due_from=h.clock.now() + timedelta(days=1))
    assert error.value.code == "not_today"
    identifier = add_recheck(h, "insight_1")
    with pytest.raises(RecheckRejected) as error:
        add_recheck(h, "insight_1")
    assert error.value.code == "already_added"
    with pytest.raises(RecheckRejected) as error:
        add_recheck(h, "insight_2")
    assert error.value.code == "capacity"
    with h.db.snapshot() as view:
        occ = h.engine.deps.store.get(view, identifier)
        assert occ is not None and occ.cause_kind == "assistant" and not occ.periodic
        assert (
            h.engine.deps.store.latest_periodic_final_day(view, "person_mara", "vision_self_check") == before
        )
        periodic = h.engine.deps.store.periodic_on_day(view, "person_mara", "vision_self_check", 1)
        assert periodic is not None and periodic.cause_key == "cadence"


@pytest.mark.trace("T-E25-010", "T-E25-094", "INV-E25-05")
def test_assistant_quota_remains_separate_when_general_capacity_is_full(tmp_path: Path) -> None:
    h = morning(
        tmp_path, **{"routine.max_open_per_person": 1, "routine.max_assistant_rechecks_per_person_day": 1}
    )
    assert h.rows(
        "SELECT count(*) FROM routine_occurrence WHERE person_id='person_mara' AND cause_kind<>'assistant'"
    ) == ((1,),)
    existing = cast(
        str, h.rows("SELECT occurrence_id FROM routine_occurrence WHERE person_id='person_mara'")[0][0]
    )
    insight = max(
        (f"insight_hidden_{i}" for i in range(100)),
        key=lambda value: h.ids.derived("ro", "person_mara", "vision_self_check", "1", "assistant:" + value),
    )
    identifier = add_recheck(h, insight)
    assert identifier > existing
    with pytest.raises(RecheckRejected) as error:
        add_recheck(h, "insight_2")
    assert error.value.code == "capacity"
    assert h.rows(
        "SELECT count(*) FROM routine_occurrence WHERE person_id='person_mara' AND cause_kind='assistant'"
    ) == ((1,),)


@pytest.mark.trace("T-E25-042", "INV-E25-01")
def test_drill_completion_true_then_false_and_skill_ids_from_release(tmp_path: Path) -> None:
    h = morning(tmp_path)
    identifier = occurrence_id(h, "emergency_drill")
    drill = h.runtime.drill_occurrence(identifier)
    assert drill is not None and drill.skill_ids == ("pulse_check_wrist", "cpr")
    assert h.runtime.complete(identifier, h.clock.now(), Actor("person", "person_mara"))
    assert not h.runtime.complete(identifier, h.clock.now(), Actor("person", "person_mara"))
    assert not h.runtime.complete("ro_absent", h.clock.now(), Actor("person", "person_mara"))
    assert h.queries.occurrence(session(), identifier)["state"] == "done"


@pytest.mark.trace("T-E25-042", "T-E25-091", "INV-E25-03")
def test_completion_recomputes_hold_from_persisted_intervals_when_deadline_lags(tmp_path: Path) -> None:
    from crew_health.routine.domain.bindings import OverlayInput

    h = morning(tmp_path)
    identifier = occurrence_id(h, "daily_check")
    with h.db.snapshot() as view:
        occ = h.engine.deps.store.get(view, identifier)
        assert occ is not None and occ.due_from is not None and occ.due_until is not None
        start, end = occ.due_from, occ.due_until
    # Persist the interval without running the deadline recompute, exercising the
    # independent read-time backup when the routine scheduler is behind.
    with h.db.begin(actor=Actor("engine", "engine_test"), correlation_id=h.ids.new("cor")) as uow:
        h.engine.deps.store.upsert_overlay(
            uow,
            OverlayInput(
                "ovl_lagged",
                1,
                "person_mara",
                "sleep",
                "ended",
                None,
                start,
                start + timedelta(hours=1),
                None,
            ),
        )
    h.advance(end + timedelta(minutes=30))
    assert h.runtime.complete(identifier, h.clock.now(), Actor("person", "person_mara"))
    assert h.queries.occurrence(session(), identifier)["state"] == "done"


@pytest.mark.trace("T-E25-010", "T-E25-094")
def test_assistant_recheck_before_clock_epoch_is_rejected_as_not_today(tmp_path: Path) -> None:
    h = morning(tmp_path)
    with pytest.raises(RecheckRejected) as error:
        add_recheck(h, "insight_pre_epoch", due_from=h.clock.now() - timedelta(days=1))
    assert error.value.code == "not_today"


@pytest.mark.trace("T-E25-001", "T-E25-004", "INV-E25-01")
def test_later_then_skip_uses_current_window_and_final_state_is_immutable(tmp_path: Path) -> None:
    h = morning(tmp_path)
    identifier = occurrence_id(h, "daily_check")
    current = h.queries.occurrence(session(), identifier)
    until = datetime.fromisoformat(cast(str, current["due_until"]))
    invalid = (
        OccurrenceActionBody("skip"),
        OccurrenceActionBody("skip", skip_reason="declined", answer_ids=("ans_linked",)),
        OccurrenceActionBody("later"),
        OccurrenceActionBody("later", later_until=h.clock.now()),
        OccurrenceActionBody("later", later_until=until + timedelta(seconds=1)),
    )
    for index, body in enumerate(invalid):
        with pytest.raises(InvalidBody):
            h.runtime.act(
                session(),
                identifier,
                body,
                if_match=1,
                idempotency_key=f"cmd_invalid_action_{index}",
                config_release=None,
            )
    assert h.queries.occurrence(session(), identifier)["revision"] == 1
    later_at = h.clock.now() + timedelta(hours=1)
    status, delayed = h.runtime.act(
        session(),
        identifier,
        OccurrenceActionBody("later", later_until=later_at),
        if_match=1,
        idempotency_key="cmd_later",
        config_release=None,
    )
    assert status == 200 and delayed["state"] == "later" and delayed["revision"] == 2
    h.advance(later_at)
    assert h.queries.occurrence(session(), identifier)["state"] == "due"
    _, skipped = h.runtime.act(
        session(),
        identifier,
        OccurrenceActionBody("skip", skip_reason="not_available", note="private skip reason"),
        if_match=2,
        idempotency_key="cmd_skip",
        config_release=None,
    )
    assert skipped["state"] == "skipped" and skipped["revision"] == 3 and skipped["outcome"] is None
    assert skipped["skip"] == {
        "reason": "not_available",
        "note": "private skip reason",
        "at": "2026-11-01T09:00:00Z",
    }
    with pytest.raises(InvalidTransition):
        h.runtime.act(
            session(),
            identifier,
            OccurrenceActionBody("complete"),
            if_match=3,
            idempotency_key="cmd_after_final",
            config_release=None,
        )
    assert h.queries.occurrence(session(), identifier) == skipped


@pytest.mark.trace("T-E25-005", "T-E25-091", "INV-E25-03")
def test_held_task_rejects_later_but_still_allows_person_to_skip(tmp_path: Path) -> None:
    h = morning(tmp_path)
    identifier = occurrence_id(h, "daily_check")
    h.overlay(started_at=h.clock.now())
    h.deliver()
    held = h.queries.occurrence(session(), identifier)
    assert held["suppressed_by_overlay"] == "sleep"
    assert held["allowed_actions"] == ["complete", "skip"]
    with pytest.raises(InvalidTransition):
        h.runtime.act(
            session(),
            identifier,
            OccurrenceActionBody("later", later_until=h.clock.now() + timedelta(hours=1)),
            if_match=cast(int, held["revision"]),
            idempotency_key="cmd_held_later",
            config_release=None,
        )
    _, skipped = h.runtime.act(
        session(),
        identifier,
        OccurrenceActionBody("skip", skip_reason="no_time"),
        if_match=cast(int, held["revision"]),
        idempotency_key="cmd_held_skip",
        config_release=None,
    )
    assert skipped["state"] == "skipped" and skipped["outcome"] is None


@pytest.mark.trace("T-E25-002", "T-E25-015", "INV-E25-01")
def test_act_on_virtual_occurrence_materializes_only_the_accepted_transaction(tmp_path: Path) -> None:
    h = morning(tmp_path)
    h.advance(datetime(2026, 11, 2, 8, tzinfo=UTC))
    identifier = h.ids.derived("ro", "person_mara", "daily_check", "2", "cadence")
    assert h.queries.occurrence(session(), identifier)["revision"] == 1
    assert h.rows("SELECT count(*) FROM routine_occurrence WHERE occurrence_id=?", (identifier,)) == ((0,),)
    with pytest.raises(Unauthorized):
        h.runtime.act(
            session(),
            identifier,
            OccurrenceActionBody("complete", observation_ids=("obs_unverified",)),
            if_match=1,
            idempotency_key="cmd_virtual_rejected",
            config_release=None,
        )
    assert h.rows("SELECT count(*) FROM routine_occurrence WHERE occurrence_id=?", (identifier,)) == ((0,),)
    _, accepted = h.runtime.act(
        session(),
        identifier,
        OccurrenceActionBody("skip", skip_reason="declined"),
        if_match=1,
        idempotency_key="cmd_virtual_accepted",
        config_release=None,
    )
    assert accepted["state"] == "skipped" and accepted["revision"] == 2
    assert h.rows("SELECT state,revision FROM routine_occurrence WHERE occurrence_id=?", (identifier,)) == (
        ("skipped", 2),
    )
    assert h.rows(
        "SELECT from_state,to_state FROM routine_occurrence_transition WHERE occurrence_id=? ORDER BY revision",
        (identifier,),
    ) == ((None, "due"), ("due", "skipped"))
    assert h.rows("SELECT command_key FROM platform_command_ledger WHERE resource_ref=?", (identifier,)) == (
        ("cmd_virtual_accepted",),
    )


@pytest.mark.trace("T-E25-014", "T-E25-042", "INV-E25-07")
def test_today_facades_take_fresh_private_snapshots_after_a_completion(tmp_path: Path) -> None:
    h = morning(tmp_path)
    initial = h.runtime.today_summary(session(), h.clock.now())
    due = tuple(h.runtime.due_today("person_mara", h.clock.now()))
    assert due and initial.due == len(due) and initial.open_today == initial.due + initial.later
    assert all(ref.state == "due" for ref in due)
    other = h.runtime.today_summary(session("person_tomas"), h.clock.now())
    other_ids = {ref.occurrence_id for ref in h.runtime.due_today("person_tomas", h.clock.now())}
    assert not other_ids.intersection(ref.occurrence_id for ref in due)
    identifier = occurrence_id(h, "daily_check")
    h.runtime.act(
        session(),
        identifier,
        OccurrenceActionBody("complete"),
        if_match=1,
        idempotency_key="cmd_today_complete",
        config_release=None,
    )
    current = h.runtime.today_summary(session(), h.clock.now())
    assert current.due == initial.due - 1 and current.done == initial.done + 1
    assert identifier not in current.next_occurrence_ids and identifier not in {
        ref.occurrence_id for ref in h.runtime.due_today("person_mara", h.clock.now())
    }
    assert any(ref.occurrence_id == identifier for ref in due)
    assert h.runtime.today_summary(session("person_tomas"), h.clock.now()) == other
    with pytest.raises(Unauthorized):
        h.runtime.today_summary(session(None), h.clock.now())


@pytest.mark.trace("T-E25-042", "INV-E25-01", "INV-E25-05")
def test_drill_queries_unknown_final_and_assistant_access_fail_closed(tmp_path: Path) -> None:
    h = morning(tmp_path)
    assert h.runtime.drill_occurrence("ro_unknown") is None
    identifier = occurrence_id(h, "emergency_drill")
    with pytest.raises(Unauthorized):
        h.runtime.complete(identifier, h.clock.now(), Actor("assistant", "assistant_1"))
    assert h.runtime.complete(identifier, h.clock.now(), Actor("person", "person_mara"))
    final = h.runtime.drill_occurrence(identifier)
    assert final is not None and final.state == "done" and final.person_id == "person_mara"
    assert not h.runtime.complete(identifier, h.clock.now(), Actor("person", "person_mara"))
    with pytest.raises(Unauthorized):
        h.runtime.act(
            session(None),
            identifier,
            OccurrenceActionBody("skip", skip_reason="declined"),
            if_match=2,
            idempotency_key="cmd_station_final",
            config_release=None,
        )
