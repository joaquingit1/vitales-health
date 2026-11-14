"""E23 section 12 mission portions of T-E23-051, 055, 057, 069, 090, 092 on kernel fakes.

Waiting on E01 T2 (real SQLite, the idempotency ledger, HTTP): the receipt, retry and changed-target parts of
T-E23-052 and T-E23-089, disk-full against a real file, and process kill tests. Crash points here are injected
failures of the fake store inside one unit of work, with the fake store rolled back as a transaction would.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from e23m_helpers import START, Station, build_station, documents, edited, make_release, operator, person

from crew_health.contracts_gen.enums import ConfigName
from crew_health.mission.application.init import initialize_mission
from crew_health.mission.application.queries import event_model
from crew_health.mission.application.use_cases import (
    Caller,
    EventTimeFacts,
    act_on_change,
    create_overlay,
    propose_change,
    record_event,
)

pytestmark = pytest.mark.e23


def crew(person_id: str = "per_a") -> Caller:
    return person(person_id, "crew")


def fingerprint(station: Station) -> tuple[object, ...]:
    state = station.factory.state
    return (
        station.store.copy_rows(),
        len(state.events),
        len(state.audit),
        len(state.replication),
        dict(state.schedules),
    )


def init(station: Station, environment: str = "lunar_surface") -> None:
    with station.run(operator().actor) as uow:
        initialize_mission(
            station.services,
            uow,
            operator(),
            mission_mode="SIM",
            planned_length_days=None,
            site_node_id="node",
            environment_id=environment,
        )


def crash_at_every_write(station: Station, command: Callable[[Station], object]) -> int:
    """Fail the command after 0, 1, 2 ... writes. Each failure leaves nothing; the first success is one effect."""
    for allowed in range(0, 40):
        before = fingerprint(station)
        station.store.fail_after = station.store.writes + allowed
        try:
            command(station)
        except RuntimeError:
            assert fingerprint(station) == before, f"partial state after {allowed} writes"
            continue
        finally:
            station.store.fail_after = None
        return allowed
    raise AssertionError("the command never completed")


@pytest.mark.trace("T-E23-051", "AGENTS6", "3.3")
def test_a_crash_at_each_step_of_init_proposal_confirmation_overlay_and_event_leaves_nothing_partial() -> (
    None
):
    station = build_station()
    steps = crash_at_every_write(station, lambda s: _run_init(s))
    assert steps >= 1 and len(station.store.profile_rows) == 1
    for command in (
        lambda s: _run_propose(s),
        lambda s: _run_confirm(s),
        lambda s: _run_overlay(s),
        lambda s: _run_event(s),
    ):
        crash_at_every_write(station, command)
    assert sorted(r.state for r in station.store.profile_rows.values()) == ["active", "superseded"]
    assert (
        len([r for r in station.store.overlay_rows.values() if r.kind == "eva" and r.state == "ended"]) == 1
    )
    assert len(station.store.event_rows) == 1  # retries of failed attempts never duplicated the event


def _run_init(s: Station) -> None:
    with s.run(operator().actor) as uow:
        initialize_mission(
            s.services,
            uow,
            operator(),
            mission_mode="SIM",
            planned_length_days=None,
            site_node_id="node",
            environment_id="station_leo",
        )


def _run_propose(s: Station) -> None:
    with s.run(operator().actor) as uow:
        propose_change(
            s.services, uow, operator(), environment_id="lunar_surface", reason="operator", effective_at=None
        )


def _run_confirm(s: Station) -> None:
    proposal = next(r for r in s.store.profile_rows.values() if r.state == "proposed")
    with s.run(crew().actor) as uow:
        act_on_change(
            s.services,
            uow,
            crew(),
            profile_id=proposal.profile_id,
            action="confirm",
            expected_revision=proposal.revision,
        )


def _run_overlay(s: Station) -> None:
    with s.run(crew().actor) as uow:
        create_overlay(
            s.services, uow, crew(), kind="eva", planned_start=None, started_at=START, location=None
        )


def _run_event(s: Station) -> None:
    with s.run(crew().actor) as uow:
        record_event(
            s.services,
            uow,
            crew(),
            event_type="eva_ended",
            occurred_at=START + timedelta(hours=1),
            person_ids=("per_a",),
            location=None,
            note=None,
            origin_mode="SIM",
            time=EventTimeFacts("known", START + timedelta(hours=1)),
        )


@pytest.mark.trace("T-E23-057", "AGENTS7", "10")
def test_a_mission_event_note_is_in_no_log_audit_outbox_replication_or_repr(station: Station) -> None:
    init(station)
    secret = "private words about per_b 7f3a"
    with station.run(crew().actor) as uow:
        stored = record_event(
            station.services,
            uow,
            crew(),
            event_type="eva_planned",
            occurred_at=START,
            person_ids=("per_b",),
            location=None,
            note=secret,
            origin_mode="SIM",
            time=EventTimeFacts("known", START),
        )
    state = station.factory.state
    assert secret not in repr(stored) and secret not in repr(station.store.event_rows)
    blobs = [e.data_json.decode() for e in state.events]
    blobs += [repr(a) for a in state.audit] + [c.payload_json.decode() for c in state.replication]
    assert all(secret not in blob for blob in blobs)
    assert (
        secret in event_model(stored).model_dump_json()
    )  # only an authorized read from the owning store has it


@pytest.mark.trace("T-E23-069", "T-E23-090", "D1", "D8", "I-O5")
def test_open_work_keeps_its_pins_across_a_release_and_a_release_edit_never_removes_an_active_profile() -> (
    None
):
    station = build_station()
    init(station, "station_leo")
    with station.run(crew().actor) as uow:
        overlay = create_overlay(
            station.services, uow, crew(), kind="sleep", planned_start=None, started_at=START, location=None
        )
    narrowed = edited(
        documents(),
        ConfigName.ENVIRONMENT_PROFILES,
        lambda d: d["change_confirmation"].__setitem__("confirmations_required", 2),
    )
    with station.run(operator().actor, release=make_release("rel_test_0002", narrowed)) as uow:
        proposal = propose_change(
            station.services, uow, operator(), environment_id="transit", reason="operator", effective_at=None
        )
    assert proposal.confirmations_required == 2 and proposal.proposal_release_id == "rel_test_0002"
    assert station.store.overlay_rows[overlay.overlay_id].pinned_release_id == "rel_test_0001"
    first = next(r for r in station.store.profile_rows.values() if r.state == "active")
    assert (
        first.config_release_id == "rel_test_0001" and first.state == "active"
    )  # not rewritten by the new release


@pytest.mark.trace("T-E23-092", "AGENTS6", "S5.11")
def test_the_overlay_interval_read_raises_over_its_bound_and_never_returns_a_truncated_list(
    station: Station,
) -> None:
    init(station)
    for minute in range(3):
        with station.run(crew().actor) as uow:
            record = create_overlay(
                station.services,
                uow,
                crew(),
                kind="exercise",
                planned_start=None,
                started_at=START + timedelta(hours=minute),
                location=None,
            )
        from crew_health.mission.application.use_cases import end_overlay

        with station.run(crew().actor) as uow:
            end_overlay(
                station.services,
                uow,
                crew(),
                overlay_id=record.overlay_id,
                ended_at=START + timedelta(hours=minute, minutes=10),
                expected_revision=record.revision,
            )
    span = (START, START + timedelta(days=1))
    assert len(station.state.intervals("per_a", {"exercise"}, *span, 3)) == 3  # exactly at the bound
    with pytest.raises(Exception, match="above the bound"):
        station.state.intervals("per_a", {"exercise"}, *span, 2)  # one over
