"""Acceptance boundaries through registered facades and the real durable ledger."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ConfigName, Role
from crew_health.kernel.commands import CommandIdentity, IdempotencyStore, StoredResult
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.uow import SessionContext, UnitOfWork
from crew_health.routine.application.ports import OccurrenceActionBody
from crew_health.routine.domain.errors import ActionNotAllowed

from .harness import session
from .test_commands_provided import manual_body, morning, occurrence_id, officer


@pytest.mark.trace("T-E25-006", "INV-E25-07")
def test_manual_all_crew_audience_is_frozen_from_the_available_roster(tmp_path: Path) -> None:
    h = morning(tmp_path)
    body = replace(manual_body(), person_id=None)
    status, created = h.runtime.create_override(
        officer(), body, idempotency_key="cmd_all_crew", config_release=None
    )
    identifier = cast(str, created["override_id"])
    assert status == 201 and created["audience"] == "all_crew" and created["person_id"] is None
    assert h.rows(
        "SELECT person_id,member_basis FROM routine_override_member WHERE override_id=? ORDER BY person_id",
        (identifier,),
    ) == tuple((person, "site") for person in sorted(h.roster.person_ids()))
    others = h.runtime.overrides(session("person_tomas"), None, 100)
    assert identifier in {item["override_id"] for item in cast(list[dict[str, object]], others["items"])}


@pytest.mark.trace("T-E25-006", "INV-E25-07")
def test_manual_group_requires_roster_but_explicit_person_does_not(tmp_path: Path) -> None:
    h = morning(tmp_path)
    h.restart(replace(h.engine.deps, roster=None))
    with pytest.raises(ActionNotAllowed) as missing:
        h.runtime.create_override(
            officer(),
            replace(manual_body(), person_id=None),
            idempotency_key="cmd_no_roster_group",
            config_release=None,
        )
    assert missing.value.reason == "roster_unavailable"
    assert h.rows("SELECT count(*) FROM routine_override") == ((0,),)
    status, created = h.runtime.create_override(
        officer(), manual_body(), idempotency_key="cmd_no_roster_person", config_release=None
    )
    assert status == 201 and created["person_id"] == "person_mara"
    assert h.rows("SELECT command_key FROM platform_command_ledger") == (("cmd_no_roster_person",),)


@pytest.mark.trace("T-E25-006", "T-E25-022", "INV-E25-07")
def test_override_replay_rechecks_person_and_current_stop_role(tmp_path: Path) -> None:
    h = morning(tmp_path)
    h.grant_officer_care()
    _, created = h.runtime.create_override(
        officer(), manual_body(), idempotency_key="cmd_replay_create", config_release=None
    )
    identifier = cast(str, created["override_id"])
    changed_person: SessionContext = replace(officer(), person_id="person_tomas")
    assert (
        h.runtime.create_override(
            changed_person, manual_body(), idempotency_key="cmd_replay_create", config_release=None
        )[1]
        == created
    )
    _, ended = h.runtime.end_override(officer(), identifier, if_match=1, idempotency_key="cmd_replay_end")
    assert ended["state"] == "ended"
    with pytest.raises(ActionNotAllowed):
        h.runtime.end_override(
            replace(officer(), roles=frozenset({Role.CREW})),
            identifier,
            if_match=None,
            idempotency_key="cmd_replay_end",
        )
    assert (
        h.runtime.end_override(changed_person, identifier, if_match=None, idempotency_key="cmd_replay_end")[1]
        == ended
    )
    assert h.rows("SELECT revision,state FROM routine_override WHERE override_id=?", (identifier,)) == (
        (2, "ended"),
    )


@pytest.mark.trace("T-E25-006", "INV-E25-01")
def test_manual_override_capacity_rejects_without_a_ledger_or_partial_override(tmp_path: Path) -> None:
    h = morning(tmp_path, **{"routine.max_active_overrides_per_person": 1})
    h.runtime.create_override(
        officer(), manual_body(), idempotency_key="cmd_capacity_first", config_release=None
    )
    body = replace(manual_body(), item_id="body_temperature", trigger_index=0)
    with pytest.raises(ActionNotAllowed) as capacity:
        h.runtime.create_override(officer(), body, idempotency_key="cmd_capacity_second", config_release=None)
    assert capacity.value.reason == "capacity"
    assert h.rows("SELECT count(*) FROM routine_override") == ((1,),)
    assert h.rows("SELECT command_key FROM platform_command_ledger") == (("cmd_capacity_first",),)


class FailAfterLedgerRecord:
    """Storage fault injected after the real ledger has staged its durable receipt."""

    def __init__(self, ledger: IdempotencyStore) -> None:
        self.ledger = ledger

    def lookup(self, uow: UnitOfWork, identity: CommandIdentity) -> StoredResult | None:
        return self.ledger.lookup(uow, identity)

    def record(self, uow: UnitOfWork, identity: CommandIdentity, result: StoredResult) -> None:
        self.ledger.record(uow, identity, result)
        raise StorageUnavailable("disk_full")


@pytest.mark.trace("T-E25-002", "INV-E25-16", "INV-E25-07")
def test_storage_fault_after_real_receipt_rolls_back_the_whole_command_then_retry_succeeds(
    tmp_path: Path,
) -> None:
    h = morning(tmp_path)
    identifier = occurrence_id(h, "daily_check")
    deps = h.engine.deps
    h.restart(replace(deps, idempotency=FailAfterLedgerRecord(h.ledger)))
    before = h.rows("SELECT revision,state FROM routine_occurrence WHERE occurrence_id=?", (identifier,))
    history = h.rows(
        "SELECT count(*) FROM routine_occurrence_transition WHERE occurrence_id=?", (identifier,)
    )
    with pytest.raises(StorageUnavailable) as failure:
        h.runtime.act(
            session(),
            identifier,
            OccurrenceActionBody("complete", note="private record"),
            if_match=1,
            idempotency_key="cmd_disk_full",
            config_release=None,
        )
    assert failure.value.cause == "disk_full"
    assert (
        h.rows("SELECT revision,state FROM routine_occurrence WHERE occurrence_id=?", (identifier,)) == before
    )
    assert (
        h.rows("SELECT count(*) FROM routine_occurrence_transition WHERE occurrence_id=?", (identifier,))
        == history
    )
    assert h.rows("SELECT count(*) FROM platform_command_ledger WHERE command_key='cmd_disk_full'") == ((0,),)
    assert h.rows("SELECT count(*) FROM routine_record_version") == ((0,),)
    assert h.rows("SELECT count(*) FROM routine_command_version") == ((0,),)
    h.restart(deps)
    status, accepted = h.runtime.act(
        session(),
        identifier,
        OccurrenceActionBody("complete", note="private record"),
        if_match=1,
        idempotency_key="cmd_disk_full",
        config_release=None,
    )
    assert status == 200 and accepted["state"] == "done"
    assert h.rows("SELECT count(*) FROM platform_command_ledger WHERE command_key='cmd_disk_full'") == ((1,),)


@pytest.mark.trace("INV-E25-11", "T-E25-014")
def test_missing_ui_copy_serves_ids_and_scheduler_miss_commits(tmp_path: Path) -> None:
    h = morning(tmp_path)
    documents = dict(h.release.documents) if isinstance(h.release, MemoryRelease) else {}
    documents.pop(ConfigName.UI_COPY)
    missing_words = MemoryRelease("rel_no_words", documents)
    h.db.set_release_provider(lambda: missing_words)
    h.advance(h.clock.now() + timedelta(days=1))
    h.restart(h.engine.deps)
    identifier = h.ids.derived("ro", "person_mara", "exercise_log", "2", "cadence")
    assert h.runtime.occurrence(session(), identifier)["occurrence_id"] == identifier
    h.advance(h.clock.now().replace(hour=17))
    for _ in range(10):
        if h.dispatch() == 0:
            break
    assert h.rows(
        "SELECT state,miss_counted FROM routine_occurrence WHERE occurrence_id=?", (identifier,)
    ) == (("missed", 1),)
    assert any(
        component.component == "routine" and component.code == "CONFIG_INVALID"
        for component in h.health.snapshot().components
    )


@pytest.mark.trace("T-E25-002", "INV-E25-01")
def test_virtual_action_validation_and_capacity_reject_without_materializing(tmp_path: Path) -> None:
    from typing import Literal

    from crew_health.routine.domain.errors import InvalidBody

    h = morning(tmp_path, **{"routine.max_open_per_person": 1})
    identifier = h.ids.derived("ro", "person_mara", "vision_self_check", "1", "cadence")
    assert h.runtime.occurrence(session(), identifier)["revision"] == 1
    invalid = OccurrenceActionBody(cast(Literal["complete", "skip", "later"], "unrecognized"))
    with pytest.raises(InvalidBody) as malformed:
        h.runtime.act(
            session(),
            identifier,
            invalid,
            if_match=1,
            idempotency_key="cmd_virtual_invalid",
            config_release=None,
        )
    assert malformed.value.pointer == "/action"
    with pytest.raises(ActionNotAllowed) as full:
        h.runtime.act(
            session(),
            identifier,
            OccurrenceActionBody("skip", skip_reason="declined"),
            if_match=1,
            idempotency_key="cmd_virtual_capacity",
            config_release=None,
        )
    assert full.value.reason == "capacity"
    assert h.rows("SELECT count(*) FROM routine_occurrence WHERE occurrence_id=?", (identifier,)) == ((0,),)
    assert h.rows("SELECT count(*) FROM platform_command_ledger") == ((0,),)
