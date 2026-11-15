"""Failure boundaries remain durable, private and honest with optional providers absent."""

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.uow import Actor, Audience, AudienceProjectionKey, AudienceRule, SessionContext
from crew_health.routine.domain.errors import InvalidBody

from . import harness
from .harness import instant, session, station
from .test_integration_inputs import at, audited, drain_timers


def audience(rule: str, identifier: str) -> Audience:
    event_type = "routine.override_changed" if rule == "routine.override" else "routine.occurrence_changed"
    return Audience(rule, identifier, AudienceProjectionKey("routine", event_type, 1), "1")


@pytest.mark.trace("T-E25-014", "INV-E25-11")
def test_missing_optional_words_never_prevents_a_durable_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = harness.configured_release

    def without_words(release_id: str = "rel_test") -> MemoryRelease:
        release = original(release_id)
        return MemoryRelease(
            release_id,
            {name: doc for name, doc in release.documents.items() if name is not ConfigName.UI_COPY},
        )

    monkeypatch.setattr(harness, "configured_release", without_words)
    hub = station(tmp_path, at=at(15))
    assert hub.rows("SELECT COUNT(*) FROM events_outbox") == ((0,),)
    target = hub.rows(
        "SELECT occurrence_id FROM routine_occurrence WHERE person_id=? AND item_id='exercise_log'",
        ("person_mara",),
    )[0][0]
    hub.advance(at(17))
    drain_timers(hub)
    assert hub.rows("SELECT state,miss_counted FROM routine_occurrence WHERE occurrence_id=?", (target,)) == (
        ("missed", 1),
    )
    assert hub.rows("SELECT COUNT(*) FROM events_outbox") == ((0,),)
    assert any(component.code == "CONFIG_INVALID" for component in hub.health.snapshot().components)


@pytest.mark.trace("T-E25-014", "INV-E25-16")
def test_input_with_no_mission_facts_is_audited_without_state_changes(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    before = hub.rows("SELECT occurrence_id,revision,state FROM routine_occurrence ORDER BY occurrence_id")
    hub.mission.facts = None
    event = hub.raise_warning()
    hub.deliver()
    assert (
        hub.rows("SELECT occurrence_id,revision,state FROM routine_occurrence ORDER BY occurrence_id")
        == before
    )
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((0,),)
    assert audited(hub, "mission_unknown")
    with hub.db.snapshot() as view:
        assert hub.events.cursor(view.connection, "routine.inputs") >= event.seq


@pytest.mark.trace("T-E25-096", "INV-E25-16")
def test_startup_provider_failure_logs_its_class_and_keeps_module_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def unavailable(*args: object) -> None:
        raise StorageUnavailable("disk_full")

    monkeypatch.setattr(harness.Mission, "current", unavailable)
    with caplog.at_level("ERROR", logger="crew_health.routine"):
        hub = station(tmp_path, at=at(9))
    assert hub.engine is hub.runtime.engine
    assert hub.commands is hub.runtime.commands
    assert "StorageUnavailable" in caplog.text
    assert hub.rows("SELECT COUNT(*) FROM routine_occurrence") == ((0,),)
    assert any(
        component.component == "routine" and component.code == "EVALUATION_UNAVAILABLE"
        for component in hub.health.snapshot().components
    )


@pytest.mark.trace("T-E25-096", "INV-E25-16")
def test_unreadable_contract_body_callback_is_audited_and_skipped(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    event = hub.raise_warning()
    with hub.db.begin(actor=Actor("engine", "events_worker"), correlation_id="cor_bad_body") as uow:
        hub.runtime.event(uow, replace(event, data_json=b'{"warning_id":42}'))
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((0,),)
    assert audited(hub, "input_unreadable")


@pytest.mark.trace("T-E25-029", "INV-E25-01")
def test_audience_rules_recheck_person_and_projection_on_each_snapshot(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    identifier = str(
        hub.rows("SELECT occurrence_id FROM routine_occurrence WHERE person_id=? LIMIT 1", ("person_mara",))[
            0
        ][0]
    )
    rule = cast(AudienceRule, hub.registry.declarations["audiences"]["routine.occurrence"])
    allowed = audience("routine.occurrence", identifier)
    with hub.db.snapshot() as view:
        assert rule.admits(view, session(), allowed)
        assert not rule.admits(view, session("person_tomas"), allowed)
        assert not rule.admits(view, session(None, roles=frozenset({Role.CREW_MEDICAL_OFFICER})), allowed)
        assert not rule.admits(view, session(), replace(allowed, policy_version="2"))
        assert not rule.admits(
            view,
            session(),
            replace(allowed, projection=AudienceProjectionKey("routine", "wrong.projection", 1)),
        )
        assert not rule.admits(view, session(), audience("routine.occurrence", "ro_missing"))


@pytest.mark.trace("T-E25-029", "INV-E25-01")
def test_person_override_is_private_while_group_override_admits_members_and_officers(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.overlay("ovl_person_ended", kind="eva", state="ended", revision=2, started_at=at(7), ended_at=at(9))
    hub.raise_warning()
    hub.deliver()
    personal = str(hub.rows("SELECT override_id FROM routine_override WHERE audience='person' LIMIT 1")[0][0])
    group = str(
        hub.rows("SELECT override_id FROM routine_override WHERE audience='everyone_in_location' LIMIT 1")[0][
            0
        ]
    )
    rule = cast(AudienceRule, hub.registry.declarations["audiences"]["routine.override"])
    officer = session("person_ilya", roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
    ground = SessionContext(
        "session_ground", "ground", Actor("ground", "ground_1"), None, frozenset({Role.GROUND})
    )
    with hub.db.snapshot() as view:
        assert rule.admits(view, session(), audience("routine.override", personal))
        assert not rule.admits(view, officer, audience("routine.override", personal))
        assert not rule.admits(view, ground, audience("routine.override", personal))
        assert rule.admits(view, session(), audience("routine.override", group))
        assert rule.admits(view, officer, audience("routine.override", group))
        assert rule.admits(view, ground, audience("routine.override", group))
        assert not rule.admits(view, session("person_ilya"), audience("routine.override", group))


@pytest.mark.trace("T-E25-096", "INV-E25-16")
@pytest.mark.parametrize("domain_error", (True, False))
def test_domain_rejection_is_an_audited_outcome_but_problem_error_aborts_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain_error: bool
) -> None:
    hub = station(tmp_path, at=at(9))
    hub.deliver()
    with hub.db.snapshot() as view:
        position = hub.events.cursor(view.connection, "routine.inputs")

    def reject(*args: object) -> None:
        if domain_error:
            raise InvalidBody("/test", "domain_rejection")
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "boundary_failure")

    monkeypatch.setattr(hub.engine.inputs, "fire", reject)
    event = hub.raise_warning()
    if domain_error:
        hub.deliver()
        assert audited(hub, "input_rejected")
        with hub.db.snapshot() as view:
            assert hub.events.cursor(view.connection, "routine.inputs") >= event.seq
    else:
        with pytest.raises(ProblemError, match="boundary_failure"):
            hub.deliver()
        with hub.db.snapshot() as view:
            assert hub.events.cursor(view.connection, "routine.inputs") == position
        assert not audited(hub, "input_rejected")
    assert hub.rows("SELECT COUNT(*) FROM routine_override") == ((0,),)


@pytest.mark.trace("T-E25-007", "T-E25-017")
def test_late_warning_retains_override_without_creating_expired_occurrences(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(20, day=2))
    source_at = at(9)
    hub.raise_warning(raised_at=instant(source_at), evidence_time=instant(source_at))
    hub.deliver()
    assert hub.rows("SELECT COUNT(*) FROM routine_override WHERE state='active'") == ((2,),)
    assert hub.rows("SELECT COUNT(*) FROM routine_question_instance") == ((0,),)
    assert hub.rows("SELECT COUNT(*) FROM routine_occurrence WHERE cause_kind='override'") == ((0,),)
    assert audited(hub, "late_input")


@pytest.mark.trace("T-E25-044", "T-E25-031")
def test_real_landing_cadence_binding_creates_activations_without_overrides(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(9))
    hub.mission_event("landing")
    hub.deliver()
    assert not audited(hub, "unsupported_binding")
    assert hub.rows("SELECT COUNT(*) FROM routine_occurrence WHERE item_id='gravity_arrival_check'") == (
        (4,),
    )
    assert hub.rows(
        "SELECT COUNT(*) FROM routine_override WHERE binding_id='gravity_arrival_check.landing'"
    ) == ((0,),)
    assert hub.rows(
        "SELECT COUNT(*) FROM routine_override WHERE binding_id='blood_pressure.return_to_gravity'"
    ) == ((1,),)
