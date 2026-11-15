"""Boundary regressions discovered while mounting the routine API."""

from datetime import timedelta
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import Role
from crew_health.routine.application.ports import OccurrenceActionBody, OverrideCreateBody, OverrideUntil

from .harness import configured_release, session, station


@pytest.mark.trace("T-E25-018", "R-E25-B2")
def test_action_accepts_the_occurrences_pinned_release(tmp_path: Path) -> None:
    hub = station(tmp_path)
    identifier = str(
        hub.rows("SELECT occurrence_id FROM routine_occurrence WHERE person_id=? LIMIT 1", ("person_mara",))[
            0
        ][0]
    )
    old = hub.queries.occurrence(session(), identifier)
    hub.release = configured_release("rel_new")
    hub.db.set_release_provider(lambda: hub.release)
    assert hub.queries.occurrence(session(), identifier)["config_release_id"] == old["config_release_id"]
    status, _ = hub.commands.act(
        session(),
        identifier,
        OccurrenceActionBody("skip", skip_reason="no_time"),
        if_match=1,
        idempotency_key="pinned_key",
        config_release=str(old["config_release_id"]),
    )
    assert status == 200


@pytest.mark.trace("T-E25-027", "T-E25-101")
def test_person_override_is_visible_to_an_officer_with_a_grant_only(tmp_path: Path) -> None:
    hub = station(tmp_path)
    officer = session("person_tomas", roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
    _, override = hub.commands.create_override(
        officer,
        OverrideCreateBody("vision_self_check", 0, "person_mara", OverrideUntil("person_stops")),
        idempotency_key="private_override",
    )
    assert hub.queries.overrides(officer, None, 50)["items"] == []
    hub.grant_officer_care()
    assert hub.queries.overrides(officer, None, 50)["items"] == [override]
    ground = session(None, roles=frozenset({Role.GROUND}))
    assert hub.queries.overrides(ground, None, 50)["items"] == []


@pytest.mark.trace("T-E25-027")
def test_skip_note_never_enters_events(tmp_path: Path) -> None:
    hub = station(tmp_path)
    identifier = str(
        hub.rows("SELECT occurrence_id FROM routine_occurrence WHERE person_id=? LIMIT 1", ("person_mara",))[
            0
        ][0]
    )
    hub.commands.act(
        session(),
        identifier,
        OccurrenceActionBody("skip", skip_reason="no_time", note="private secret"),
        if_match=1,
        idempotency_key="private_skip",
    )
    assert "private secret" not in str(hub.rows("SELECT envelope FROM events_outbox"))


@pytest.mark.trace("T-E25-018")
def test_replay_after_thirty_days_returns_recorded_completion(tmp_path: Path) -> None:
    hub = station(tmp_path)
    identifier = str(
        hub.rows(
            "SELECT occurrence_id FROM routine_occurrence WHERE person_id=? AND state='due' LIMIT 1",
            ("person_mara",),
        )[0][0]
    )
    first = hub.commands.act(
        session(), identifier, OccurrenceActionBody("complete"), if_match=1, idempotency_key="thirty_day_key"
    )
    hub.advance(hub.clock.now() + timedelta(days=30))
    assert (
        hub.commands.act(
            session(),
            identifier,
            OccurrenceActionBody("complete"),
            if_match=1,
            idempotency_key="thirty_day_key",
        )
        == first
    )
