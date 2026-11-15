"""T-E01-11 V35: committed event replay, privacy, cursor and retention invariants."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import EventType, Role
from crew_health.events.adapters.notifier import BoundedNotifier
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.events.application.delivery import EventDelivery
from crew_health.events.application.retention import EventRetention
from crew_health.events.application.subscriptions import DurableSubscriptions
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    EventScope,
    LocalProjectionRegistration,
    OutboxEvent,
    SessionContext,
)
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-11", "V35", "privacy")]


class Rule:
    def __init__(self):
        self.allowed = True

    def admits(self, snapshot, session, audience):
        return self.allowed


class Sessions:
    def __init__(self):
        self.active = True

    def alive(self, snapshot, session):
        return self.active


def session(person="person_A"):
    return SessionContext("session_1", "crew", Actor("person", person), person, frozenset({Role.CREW}))


@pytest.fixture
def station(tmp_path):
    db = database(tmp_path)
    db.migrate(Path("hub/src/crew_health/events/adapters/migrations/0001_events.sql").read_text())
    repository = EventRepository(
        settings=db.settings,
        ids=db.ids,
        validator=EventValidator(schema_directory=Path("contracts/api/schemas")),
    )
    db.event_sink = repository.append
    rule = Rule()
    key = AudienceProjectionKey("guided", "beat", 1)
    registration = LocalProjectionRegistration(
        key,
        b'{"type":"object","required":["guided_check_id","beat_count","at","seconds_left"],"additionalProperties":false,"properties":{"guided_check_id":{"type":"string"},"beat_count":{"type":"integer"},"at":{"type":"string"},"seconds_left":{"type":["integer","null"]}}}',
        ("/guided_check_id", "/beat_count", "/at", "/seconds_left"),
        rule,
    )
    sessions = Sessions()
    delivery = EventDelivery(
        repository=repository,
        settings=db.settings,
        snapshots=db,
        authorizer=sessions,
        projections=(registration,),
        audiences={"guided": rule},
    )
    return db, repository, delivery, rule, sessions, key


def event(db, key, *, person="person_A", audience=True):
    return OutboxEvent(
        EventType.GUIDED_CHECK_BEAT,
        db.clock.now(),
        EventScope("person", person, None),
        db._release_provider().release_id,
        None,
        canonical_json(
            {
                "guided_check_id": "guided_1",
                "beat_count": 1,
                "at": db.clock.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "seconds_left": None,
            }
        ),
        Audience(
            "guided", "guided_1", key if audience else AudienceProjectionKey("unknown", "unknown", 1), "1"
        ),
    )


def emit(db, value):
    with db.begin(actor=Actor("engine", "engine_1"), correlation_id="correlation_1") as uow:
        return uow.emit(value)


def test_commit_and_rollback_are_the_delivery_boundary(station):
    db, _, delivery, _, _, key = station
    with (
        pytest.raises(RuntimeError),
        db.begin(actor=Actor("engine", "engine_1"), correlation_id="correlation_1") as uow,
    ):
        uow.emit(event(db, key))
        assert delivery.page(session(), after="0").events == []
        raise RuntimeError("crash")
    assert delivery.page(session(), after="0").events == []
    stored = emit(db, event(db, key))
    first = delivery.page(session(), after="0")
    second = delivery.page(session(), after="0")
    assert first == second
    assert first.events[0].event_id.root == stored.event_id
    assert first.events[0].seq == stored.seq
    assert first.events[0].occurred_at == first.events[0].emitted_at
    assert first.next_cursor == str(stored.seq)


def test_filtered_raw_scan_advances_without_leaking_payload(station):
    db, repository, delivery, _, _, key = station
    for _ in range(db.settings.integer("events.page_size")):
        emit(db, event(db, key, person="person_B"))
    visible = emit(db, event(db, key))
    hidden = delivery.page(session(), after="0")
    assert hidden.events == []
    assert hidden.has_more is True
    assert hidden.next_cursor == str(visible.seq - 1)
    restarted = EventDelivery(
        repository=repository,
        settings=db.settings,
        snapshots=db,
        authorizer=delivery.authorizer,
        projections=delivery.projections,
        audiences=delivery.audiences,
    )
    page = restarted.page(session(), after=hidden.next_cursor)
    assert [row.seq for row in page.events] == [visible.seq]
    assert not page.has_more
    assert "person_B" not in hidden.model_dump_json()


def test_missing_audience_revoked_grant_and_session_fail_closed(station):
    db, _, delivery, rule, sessions, key = station
    emit(db, event(db, key))
    emit(db, event(db, key, audience=False))
    assert len(delivery.page(session(), after="0").events) == 1
    rule.allowed = False
    assert delivery.page(session(), after="0").events == []
    sessions.active = False
    with pytest.raises(ProblemError) as error:
        delivery.page(session(), after="0")
    assert error.value.code.value == "UNAUTHENTICATED"


def test_event_data_full_conditional_schema_is_enforced(station):
    db, _, _, _, _, key = station
    with pytest.raises(ProblemError):
        emit(db, replace(event(db, key), data_json=b'{"invented":"payload"}'))
    with pytest.raises(ProblemError):
        emit(db, replace(event(db, key), scope=EventScope("crew", None, None)))
    with pytest.raises(ProblemError):
        emit(db, replace(event(db, key), data_json=b'{"guided_check_id":"guided_1","beat_count":NaN}'))


def test_subscriber_effect_and_cursor_rollback_together(station):
    db, repository, _, _, _, key = station
    db.migrate("CREATE TABLE subscriber_effect(seq INTEGER PRIMARY KEY) STRICT;")
    subscriptions = DurableSubscriptions(repository=repository, settings=db.settings, factory=db)
    failing = [True]

    def handle(uow, value):
        uow.connection.execute("INSERT INTO subscriber_effect VALUES(?)", (value.seq,))
        if failing[0]:
            raise RuntimeError("poison input")

    subscriptions.register("consumer", frozenset({EventType.GUIDED_CHECK_BEAT}), handle)
    row = emit(db, event(db, key))
    with pytest.raises(RuntimeError):
        subscriptions.process("consumer")
    with db.snapshot() as view:
        assert view.connection.rows("SELECT seq FROM subscriber_effect", (), limit=1) == ()
        assert view.connection.rows("SELECT seq FROM events_cursor", (), limit=1) == ()
    failing[0] = False
    assert subscriptions.process("consumer") == 1
    assert subscriptions.process("consumer") == 0
    with db.snapshot() as view:
        assert view.connection.rows("SELECT seq FROM subscriber_effect", (), limit=1) == ((row.seq,),)
        assert view.connection.rows("SELECT seq FROM events_cursor", (), limit=1) == ((row.seq,),)


def test_retention_waits_for_subscribers_and_rejects_arbitrary_delete(station):
    db, repository, delivery, _, _, key = station
    subscriptions = DurableSubscriptions(repository=repository, settings=db.settings, factory=db)
    subscriptions.register("consumer", frozenset({EventType.GUIDED_CHECK_BEAT}), lambda uow, value: None)
    row = emit(db, event(db, key))
    db.clock.at += timedelta(hours=db.settings.integer("events.retention_hours") + 1)
    retention = EventRetention(
        repository=repository, settings=db.settings, advance=db.retention_advance, subscribers=("consumer",)
    )
    with db.begin(actor=Actor("engine", "engine_1"), correlation_id="correlation_1") as uow:
        assert retention.prune(uow) == 0
        with pytest.raises(StorageUnavailable):
            uow.connection.execute("DELETE FROM events_outbox")
        with pytest.raises(StorageUnavailable):
            uow.connection.execute("UPDATE events_retention SET watermark=999 WHERE singleton=1")
    assert subscriptions.process("consumer") == 1
    with db.begin(actor=Actor("engine", "engine_1"), correlation_id="correlation_1") as uow:
        assert retention.prune(uow) == 1
    with pytest.raises(ProblemError) as error:
        delivery.page(session(), after="0")
    assert error.value.code.value == "CURSOR_EXPIRED"
    assert delivery.page(session(), after=str(row.seq)).events == []
    assert delivery.page(session()).next_cursor == str(row.seq)


def test_notifier_capacity_is_global_and_queue_is_bounded(station):
    db, _, _, _, _, _ = station
    notifier = BoundedNotifier(settings=db.settings, ids=db.ids)
    leases = [notifier.subscribe() for _ in range(db.settings.integer("events.max_streams"))]
    with pytest.raises(ProblemError):
        notifier.subscribe()
    for _ in range(db.settings.integer("events.stream_queue_size") + 1):
        notifier.notify()
    assert all(lease.queue.qsize() == db.settings.integer("events.stream_queue_size") for lease in leases)
    for lease in leases:
        lease.close()
    lease = notifier.subscribe()
    lease.close()


def test_sse_rechecks_grant_and_session_and_uses_contract_heartbeat(station):
    import asyncio

    from crew_health.events.api.router import stream_events

    db, _, delivery, rule, sessions, key = station
    emit(db, event(db, key))
    initial = delivery.page(session(), after="0")
    rule.allowed = False
    seen = []

    async def disconnected():
        return False

    async def timeout(lease, seconds):
        seen.append(seconds)
        raise TimeoutError()

    async def run():
        notifier = BoundedNotifier(settings=db.settings, ids=db.ids)
        lease = notifier.subscribe()
        stream = stream_events(
            delivery=delivery,
            lease=lease,
            settings=db.settings,
            session=session(),
            initial=initial,
            disconnected=disconnected,
            wait=timeout,
        )
        assert await anext(stream) == b": heartbeat\n\n"
        assert seen == [15]
        sessions.active = False
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert not notifier.streams

    asyncio.run(run())


def test_sse_exact_stored_event_envelope(station):
    import asyncio
    import json

    from crew_health.events.api.router import stream_events

    db, _, delivery, _, _, key = station
    stored = emit(db, event(db, key))

    async def disconnected():
        return False

    async def run():
        notifier = BoundedNotifier(settings=db.settings, ids=db.ids)
        lease = notifier.subscribe()
        stream = stream_events(
            delivery=delivery,
            lease=lease,
            settings=db.settings,
            session=session(),
            initial=delivery.page(session(), after="0"),
            disconnected=disconnected,
        )
        payload = (await anext(stream)).decode()
        assert payload.startswith(f"id: {stored.seq}\nevent: guided_check.beat\ndata: ")
        body = json.loads(payload.split("data: ", 1)[1])
        assert body["event_id"] == stored.event_id
        assert "audience" not in body
        await stream.aclose()
        assert not notifier.streams

    asyncio.run(run())


def test_json_route_resume_and_type_filter(station):
    import asyncio

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from httpx import ASGITransport, AsyncClient

    from crew_health.app.errors import to_envelope
    from crew_health.events.api.router import create_router

    db, _, delivery, _, _, key = station
    row = emit(db, event(db, key))
    app = FastAPI()
    app.include_router(
        create_router(
            delivery=delivery,
            notifier=BoundedNotifier(settings=db.settings, ids=db.ids),
            settings=db.settings,
            session_provider=lambda request: session(),
        )
    )

    async def error_handler(request, error):
        return JSONResponse(
            to_envelope(error, "correlation_1").model_dump(mode="json"), status_code=error.status
        )

    app.add_exception_handler(ProblemError, error_handler)

    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/v1/events?after=0", headers={"Accept": "application/json"})
            assert response.status_code == 200
            assert response.json()["events"][0]["seq"] == row.seq
            resumed = await client.get(
                "/v1/events", headers={"Accept": "application/json", "Last-Event-ID": str(row.seq)}
            )
            assert resumed.json()["events"] == []
            filtered = await client.get(
                "/v1/events?after=0&types=warning.raised", headers={"Accept": "application/json"}
            )
            assert filtered.json()["events"] == []
            conflict = await client.get(
                "/v1/events?after=0", headers={"Accept": "application/json", "Last-Event-ID": "1"}
            )
            assert conflict.status_code == 400
            assert conflict.json()["code"] == "MALFORMED_REQUEST"

    asyncio.run(run())


@pytest.mark.parametrize("value", ["", "-1", "+1", "NaN", "1.0", "1000000000000000000000", "١", " 1"])
def test_cursor_parser_rejects_non_wire_sequence(value):
    from crew_health.events.domain.cursor import cursor

    with pytest.raises(ProblemError):
        cursor(value, retained=0)


def test_retention_is_bounded_by_page_and_cannot_remove_cursor(station):
    db, repository, delivery, _, _, key = station
    for _ in range(db.settings.integer("events.page_size") + 1):
        emit(db, event(db, key))
    db.clock.at += timedelta(hours=db.settings.integer("events.retention_hours") + 1)
    retention = EventRetention(
        repository=repository, settings=db.settings, advance=db.retention_advance, subscribers=()
    )
    with db.begin(actor=Actor("engine", "engine_1"), correlation_id="correlation_1") as uow:
        assert retention.prune(uow) == db.settings.integer("events.page_size")
    assert len(delivery.page(session()).events) == 1
