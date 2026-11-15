from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import EventType
from crew_health.contracts_gen.schemas.requests_schema import GroundLinkDemoSwitch
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.events.application.subscriptions import DurableSubscriptions
from crew_health.groundlink.domain.errors import BundleConflict
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.storage import StorageSession
from crew_health.kernel.uow import Actor, StoredEvent, UnitOfWork
from crew_health.platform.domain.errors import StorageConstraintViolated

from .support import AT, ROOT, Harness, event


class FaultPageStore(EventRepository):
    """Inject historic poison payloads at the event store boundary, keeping durable SQL cursors real."""

    def __init__(self, h: Harness, events: list[StoredEvent]) -> None:
        super().__init__(
            settings=h.db.settings,
            ids=h.db.ids,
            validator=EventValidator(schema_directory=ROOT / "contracts/api/schemas"),
        )
        self.events = events

    def scan(self, connection: StorageSession, *, after: int, limit: int) -> tuple[StoredEvent, ...]:
        return tuple(value for value in self.events if value.seq > after)[:limit]


def subscriptions(
    h: Harness, payloads: list[dict[str, object]]
) -> tuple[DurableSubscriptions, list[StoredEvent]]:
    h.db.migrate(
        (ROOT / "hub/src/crew_health/events/adapters/migrations/0001_events.sql").read_text(encoding="utf-8")
    )
    stored = [
        replace(event(EventType.GROUND_MESSAGE_CHANGED, payload), seq=i + 1, event_id=f"evt_input_{i}")
        for i, payload in enumerate(payloads)
    ]
    repo = FaultPageStore(h, stored)
    runner = DurableSubscriptions(repository=repo, settings=h.db.settings, factory=h.db)
    for name, declaration in h.registry.declarations["durable_subscribers"].items():
        kinds, handler = cast(
            tuple[frozenset[EventType], Callable[[UnitOfWork, StoredEvent], None]], declaration
        )
        runner.register(cast(str, name), kinds, handler)
    return runner, stored


def queued(ref: str = "msg_good") -> dict[str, object]:
    return {
        "state": "queued",
        "ground_message_id": ref,
        "text_sha256": "a" * 64,
        "queued_at": AT.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "simulated": True,
    }


@pytest.mark.trace("R61", "E29-04")
@pytest.mark.parametrize(
    "malformed,reason",
    [
        ({"state": "queued"}, "INVALID_VALUE"),
        (dict(queued(), queued_at="bad instant private"), "ValueError"),
    ],
)
def test_bad_event_is_retried_then_quarantined_and_next_event_in_page_enqueues(
    tmp_path: Path, malformed: dict[str, object], reason: str
) -> None:
    h = Harness(tmp_path)
    runner, events = subscriptions(h, [malformed, queued()])
    for _ in range(h.runtime.services.deps.settings.subscriber_max_attempts - 1):
        with pytest.raises((ProblemError, ValueError)):
            runner.process("groundlink.outbox")
        with h.db.snapshot() as view:
            assert h.runtime.quarantined(view, 10) == ()
            assert h.outbox.status(view, "ground_message", "msg_good") is None
    assert runner.process("groundlink.outbox") == 2
    assert runner.process("groundlink.outbox") == 0
    with h.db.snapshot() as view:
        rows = h.runtime.quarantined(view, 10)
        assert len(rows) == 1
        row = rows[0]
        assert row.event_id == events[0].event_id
        assert row.subscriber == "groundlink.outbox"
        assert row.event_type == EventType.GROUND_MESSAGE_CHANGED.value
        assert (
            row.reason == reason and row.attempts == h.runtime.services.deps.settings.subscriber_max_attempts
        )
        assert row.quarantined_at == h.clock.at
        assert h.outbox.status(view, "ground_message", "msg_good") is not None
    for sql in ("UPDATE groundlink_quarantine SET reason='changed'", "DELETE FROM groundlink_quarantine"):
        # A trigger abort is a typed constraint violation, not an unavailable store (E01 review).
        with pytest.raises(StorageConstraintViolated):
            with h.db.begin(actor=Actor("engine", "tamper"), correlation_id="cor_tamper") as uow:
                uow.connection.execute(sql)


@pytest.mark.trace("R61", "E29-04")
def test_hash_conflict_is_quarantined_with_safe_reason(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.enqueue()
    runner, _ = subscriptions(h, [dict(queued("msg_0000"), text_sha256="b" * 64), queued()])
    for _ in range(h.runtime.services.deps.settings.subscriber_max_attempts - 1):
        with pytest.raises(BundleConflict):
            runner.process("groundlink.outbox")
    assert runner.process("groundlink.outbox") == 2
    with h.db.snapshot() as view:
        assert h.runtime.quarantined(view, 1)[0].reason == "bundle_conflict"
        original = h.runtime.services.deps.repository.bundle(view, "ground_message", "msg_0000")
        assert original is not None and original.content_sha256 == "a" * 64


@pytest.mark.trace("R61", "E29-04")
@pytest.mark.parametrize("subscriber", ["groundlink.outbox", "groundlink.environment"])
def test_storage_failure_is_always_retried_and_never_quarantined(
    tmp_path: Path, subscriber: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path)
    runner, _ = subscriptions(h, [queued()])

    def broken(uow: UnitOfWork, value: StoredEvent) -> None:
        raise StorageUnavailable("disk_full")

    method = "on_ground_message_changed" if subscriber == "groundlink.outbox" else "on_environment_activated"
    monkeypatch.setattr(h.runtime.services.commands, method, broken)
    if subscriber == "groundlink.environment":
        cast(FaultPageStore, runner.repository).events = [
            event(EventType.ENVIRONMENT_ACTIVATED, {"environment_id": "station_leo"})
        ]
    for _ in range(h.runtime.services.deps.settings.subscriber_max_attempts + 2):
        with pytest.raises(StorageUnavailable):
            runner.process(subscriber)
    with h.db.snapshot() as view:
        assert h.runtime.quarantined(view, 10) == ()
        # Nothing committed: the failed pass rolled back before any cursor row was written (None, E01 review).
        assert runner.repository.cursor(view.connection, subscriber) is None


@pytest.mark.trace("R61", "E29-04")
@pytest.mark.parametrize("validation", [False, True])
def test_quarantine_rolls_back_handler_writes_before_advancing_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, validation: bool
) -> None:
    h = Harness(tmp_path)
    runner, _ = subscriptions(h, [queued("msg_bad"), queued()])
    commands = h.runtime.services.commands
    original = commands.on_ground_message_changed

    def partial(uow: UnitOfWork, value: StoredEvent) -> None:
        original(uow, value)
        if b"msg_bad" in value.data_json:
            if validation:
                GroundLinkDemoSwitch.model_validate({})
            raise ValueError("private failure after write")

    monkeypatch.setattr(commands, "on_ground_message_changed", partial)
    for _ in range(h.runtime.services.deps.settings.subscriber_max_attempts - 1):
        with pytest.raises(ValueError):
            runner.process("groundlink.outbox")
    assert runner.process("groundlink.outbox") == 2
    with h.db.snapshot() as view:
        assert h.outbox.status(view, "ground_message", "msg_bad") is None
        assert h.outbox.status(view, "ground_message", "msg_good") is not None
        assert h.runtime.quarantined(view, 1)[0].reason == ("ValidationError" if validation else "ValueError")


@pytest.mark.trace("R61", "E29-04")
def test_multiple_poison_events_survive_page_rollback_without_starving_good_event(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    runner, _ = subscriptions(h, [{"state": "queued"}, {"state": "queued"}, queued()])
    bound = h.runtime.services.deps.settings.subscriber_max_attempts
    for _ in range(2 * (bound - 1)):
        with pytest.raises(ProblemError):
            runner.process("groundlink.outbox")
    assert runner.process("groundlink.outbox") == 3
    with h.db.snapshot() as view:
        assert len(h.runtime.quarantined(view, 10)) == 2
        assert h.outbox.status(view, "ground_message", "msg_good") is not None


@pytest.mark.trace("R61", "E29-04")
def test_environment_validation_failure_is_quarantined_then_valid_activation_emits(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    runner, events = subscriptions(h, [])
    repo = cast(FaultPageStore, runner.repository)
    repo.events = [
        replace(
            event(EventType.ENVIRONMENT_ACTIVATED, {"environment_id": environment}),
            seq=i + 1,
            event_id=f"evt_env_{i}",
        )
        for i, environment in enumerate(("not_an_environment", "lunar_surface"))
    ]
    for _ in range(h.runtime.services.deps.settings.subscriber_max_attempts - 1):
        with pytest.raises(ProblemError):
            runner.process("groundlink.environment")
    assert runner.process("groundlink.environment") == 2
    with h.db.snapshot() as view:
        row = h.runtime.quarantined(view, 1)[0]
        assert row.subscriber == "groundlink.environment" and row.reason == "CONFIG_INVALID"
        assert h.runtime.services.deps.repository.environment(view) == "lunar_surface"
    assert len(h.events) == 1 and h.events[0].type == EventType.GROUND_LINK_CHANGED
