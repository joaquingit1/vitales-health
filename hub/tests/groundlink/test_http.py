import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.contracts_gen.schemas.ground_link_schema import GroundLink
from crew_health.groundlink.application.ports import InboundItem
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor

from .support import AT, Harness, event, lunar_window, profiles


@pytest.mark.trace("R49", "E29-01", "D33")
@pytest.mark.parametrize(
    ("environment", "status"), [("station_leo", "up"), ("lunar_surface", "down"), ("mars_surface", "delayed")]
)
def test_get_link_validates_generated_shape_and_profile(path: Path, environment: str, status: str) -> None:
    data = profiles()
    lunar_window(data)
    h = Harness(path, environment=environment, data=data)
    response = h.client.get("/v1/ground-link")
    assert response.status_code == 200
    link = GroundLink.model_validate(response.json())
    assert link.status == status and link.source == "schedule"
    assert response.headers["Config-Release"] == "release_1"
    assert response.headers["X-Correlation-Id"]
    if status == "down":
        assert link.gap_planned and link.next_contact_at is not None
        assert link.next_contact_at.root == AT + timedelta(hours=26)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.trace("R49", "E29-01", "D33")
def test_roles_and_missing_gate_fail_closed(tmp_path: Path) -> None:
    h = Harness(tmp_path / "with")
    for role in ("ground", "station"):
        assert h.client.get("/v1/ground-link", headers={"role": role}).status_code == 200
    assert h.client.get("/v1/ground-link", headers={"role": "unknown"}).status_code == 403
    h2 = Harness(tmp_path / "without", gate=False)
    assert h2.client.get("/v1/ground-link").status_code == 401


@pytest.mark.trace("R49", "E29-01", "D33")
def test_demo_switch_replay_is_original_bytes_after_use_schedule(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    body = {"status": "down", "gap_planned": True, "next_contact_at": "2026-11-15T02:00:00Z"}
    key = {"Idempotency-Key": "command_0001"}
    response = h.client.post("/v1/demo/ground-link", json=body, headers=key)
    assert response.status_code == 200
    assert response.json()["source"] == "demo_switch"
    assert len(h.events) == 1 and h.events[0].type == EventType.GROUND_LINK_CHANGED
    assert h.events[0].audience.rule == "groundlink.link"
    back = h.client.post(
        "/v1/demo/ground-link", json={"use_schedule": True}, headers={"Idempotency-Key": "command_0002"}
    )
    assert back.status_code == 200 and back.json()["source"] == "schedule"
    replay = h.client.post("/v1/demo/ground-link", json=body, headers=key)
    assert replay.content == response.content and len(h.events) == 2
    conflict = h.client.post("/v1/demo/ground-link", json={"status": "up"}, headers=key)
    assert conflict.status_code == 409 and conflict.json()["code"] == "DUPLICATE_COMMAND"


@pytest.mark.trace("R49", "E29-01", "D33")
def test_demo_refuses_live_absent_mission_assistant_and_invalid_bodies(tmp_path: Path) -> None:
    h = Harness(tmp_path / "sim")
    headers = {"Idempotency-Key": "command_0001"}
    for body in ({"status": "up", "gap_kind": "equipment"}, {"status": "delayed", "gap_planned": True}):
        response = h.client.post("/v1/demo/ground-link", json=body, headers=headers)
        assert response.status_code == 422 and response.json()["code"] == "INVALID_VALUE"
    assert h.client.post("/v1/demo/ground-link", json={"status": "up"}).status_code == 400
    assert h.client.post("/v1/demo/ground-link", content="{", headers=headers).status_code == 400
    assert h.client.post("/v1/demo/ground-link", json={"wrong": True}, headers=headers).status_code == 400
    assert (
        h.client.post(
            "/v1/demo/ground-link", json={"status": "up"}, headers={**headers, "actor": "assistant"}
        ).status_code
        == 403
    )
    h.mission.facts.mission_mode = "LIVE"
    assert h.client.post("/v1/demo/ground-link", json={"status": "up"}, headers=headers).status_code == 404
    missing = Harness(tmp_path / "missing", mission=False)
    assert (
        missing.client.post("/v1/demo/ground-link", json={"status": "up"}, headers=headers).status_code == 404
    )
    assert missing.client.get("/v1/ground-link").json()["retryable"] is True


@pytest.mark.trace("R49", "E29-01", "D33")
def test_activation_resets_override_and_unknown_payload_does_nothing(tmp_path: Path) -> None:
    h = Harness(tmp_path, mission=False)
    engine = Actor("engine", "events")
    with h.db.begin(actor=engine, correlation_id="cor_1") as uow:
        h.runtime.services.commands.on_environment_activated(
            uow, event(EventType.ENVIRONMENT_ACTIVATED, {"environment_id": "station_leo"})
        )
    assert h.client.get("/v1/ground-link").json()["status"] == "up"
    # This subscriber works without the optional mission provider.
    h.runtime.services.deps.mission = h.mission
    from crew_health.groundlink.domain.link import DemoOverride

    with h.db.begin(actor=engine, correlation_id="cor_override") as uow:
        override = DemoOverride("down", None, None, None, False, uow.now)
        link = h.runtime.services.reader.resolve_for(uow, "station_leo", override)
        h.runtime.services.deps.repository.append(uow, "override", uow.now, link, override)
    assert h.client.get("/v1/ground-link").json()["source"] == "demo_switch"
    h.mission.facts.active_environment_id = "mars_surface"
    with h.db.begin(actor=engine, correlation_id="cor_2") as uow:
        h.runtime.services.commands.on_environment_activated(
            uow, event(EventType.ENVIRONMENT_ACTIVATED, {"profile": {"environment_id": "mars_surface"}})
        )
    assert h.client.get("/v1/ground-link").json()["source"] == "schedule"
    assert h.client.get("/v1/ground-link").json()["status"] == "delayed"
    count = len(h.events)
    with h.db.begin(actor=engine, correlation_id="cor_3") as uow:
        h.runtime.services.commands.on_environment_activated(
            uow, event(EventType.ENVIRONMENT_ACTIVATED, {"unknown": True})
        )
    assert len(h.events) == count


@pytest.mark.trace("R48", "R61", "E29-04", "EI1")
def test_26_hour_outage_keeps_300_items_then_drains_in_order(tmp_path: Path) -> None:
    data = profiles()
    lunar_window(data)
    h = Harness(tmp_path, environment="lunar_surface", data=data)
    queued_ids = h.enqueue(300)
    agent = h.runtime.agent
    for _ in range(20):
        assert agent.run_once().sent == 0
    assert not h.owner.calls
    h.clock.at += timedelta(hours=26)
    while len([c for c in h.owner.calls if c[0] == "sent"]) < 300:
        result = agent.run_once()
        assert 0 < result.sent <= 17
    sends = [c for c in h.owner.calls if c[0] == "sent"]
    assert [c[1] for c in sends] == [
        f"msg_{i:04}" for _, i in sorted(zip(queued_ids, range(300), strict=True))
    ]
    assert len({c[2] for c in sends}) == 300
    assert h.status() is not None and h.status().state == "sent"
    h.clock.at += timedelta(seconds=100)
    for _ in range(10):
        agent.run_once()
    with h.db.snapshot() as view:
        delivered = h.outbox.delivered(view, since=None, limit=31)
        assert len(delivered) == 31
        assert all(
            b.arrived_at is not None and b.sent_at is not None and b.arrived_at > b.sent_at for b in delivered
        )
        link = h.query.current(view)
        assert link is not None and link.last_contact_at is not None
    assert all(h.status(f"msg_{i:04}").state == "arrived" for i in range(300))


@pytest.mark.trace("R48", "R61", "E29-04", "EI1")
def test_owner_crash_reuses_event_identity_and_completes_on_duplicate(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.enqueue()
    h.owner.fail_after_commit = True
    assert h.runtime.agent.run_once().sent == 0
    assert h.status().state == "sending"
    h.clock.at += timedelta(seconds=1)
    assert h.runtime.agent.run_once().sent == 1
    sends = [c for c in h.owner.calls if c[0] == "sent"]
    assert len(sends) == 2 and sends[0][2] == sends[1][2]


@pytest.mark.trace("R48", "R61", "E29-04", "EI1")
def test_hung_and_absent_owners_keep_durable_sending_and_retry(tmp_path: Path) -> None:
    h = Harness(tmp_path / "hung")
    h.enqueue()
    h.owner.block = threading.Event()
    start = time.monotonic()
    result = h.runtime.agent.run_once()
    assert time.monotonic() - start < 0.5 and result.sent == 0
    with h.db.snapshot() as view:
        b = h.runtime.services.deps.repository.bundle(view, "ground_message", "msg_0000")
        assert (
            b is not None
            and b.last_error == "owner_timeout"
            and b.next_attempt_at is not None
            and b.next_attempt_at > h.clock.at
        )
    h.owner.block.set()
    h.clock.at += timedelta(seconds=1)
    for _ in range(10):
        if h.runtime.agent.run_once().sent:
            break
        time.sleep(0.005)
    assert h.status().state in ("sent", "arrived")
    absent = Harness(tmp_path / "absent", owner=False)
    absent.enqueue()
    assert absent.runtime.agent.run_once().sent == 0
    with absent.db.snapshot() as view:
        b = absent.runtime.services.deps.repository.bundle(view, "ground_message", "msg_0000")
        assert b is not None and b.state == "sending" and b.last_error == "owner_unavailable"


@pytest.mark.trace("R48", "EI5")
def test_owner_calls_can_open_their_own_real_transaction(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.enqueue()

    def transact() -> None:
        with h.db.begin(actor=Actor("engine", "owner"), correlation_id="cor_owner"):
            pass

    h.owner.before = transact
    assert h.runtime.agent.run_once().sent == 1
    h.clock.at += timedelta(seconds=10)
    h.runtime.agent.run_once()
    assert h.status().state == "arrived"


@pytest.mark.trace("R61", "V95", "EI5")
def test_inbound_waits_then_delivers_once_and_rejects_oversize_utf8(tmp_path: Path) -> None:
    data = profiles()
    lunar_window(data)
    h = Harness(tmp_path, environment="lunar_surface", data=data)
    item = InboundItem(
        "reply_1", "ground_message", "msg_1", "reply", AT, "Advice", Actor("ground", "ground_1")
    )
    assert h.inbox.receive(item) == "accepted"
    assert h.inbox.receive(item) == "duplicate"
    h.runtime.agent.run_once()
    assert not h.owner.calls
    h.clock.at += timedelta(hours=26)
    h.runtime.agent.run_once()
    assert not h.owner.calls
    h.clock.at += timedelta(seconds=100)
    h.runtime.agent.run_once()
    h.runtime.agent.run_once()
    replies = [c for c in h.owner.calls if c[0] == "reply"]
    assert (
        len(replies) == 1
        and replies[0][3] == AT
        and replies[0][4] is not None
        and replies[0][4] > AT + timedelta(hours=26)
    )
    with pytest.raises(ProblemError) as error:
        h.inbox.receive(
            InboundItem(
                "reply_2", "ground_message", "msg_1", "reply", AT, "é" * 600, Actor("ground", "ground_1")
            )
        )
    assert error.value.code == ErrorCode.INVALID_VALUE


@pytest.mark.trace("R61")
def test_last_gap_uses_window_boundaries_even_when_observed_late(tmp_path: Path) -> None:
    data = profiles()
    lunar_window(data)
    h = Harness(tmp_path, environment="lunar_surface", data=data)
    h.clock.at += timedelta(hours=1)
    h.runtime.agent.run_once()
    with h.db.snapshot() as view:
        gap = h.query.last_gap(view)
        assert gap is not None and gap.started_at == AT and gap.ended_at is None
    h.clock.at = AT + timedelta(hours=27)
    h.runtime.agent.run_once()
    with h.db.snapshot() as view:
        gap = h.query.last_gap(view)
        assert gap is not None and gap.started_at == AT and gap.ended_at == AT + timedelta(hours=26)


@pytest.mark.trace("R48", "R61", "EI5")
def test_subsecond_arrival_never_happens_before_due_in_same_second(tmp_path: Path) -> None:
    from dataclasses import replace

    h = Harness(tmp_path)
    h.enqueue()
    with h.db.begin(actor=Actor("engine", "test"), correlation_id="cor_1") as uow:
        repo = h.runtime.services.deps.repository
        original = repo.bundle(uow, "ground_message", "msg_0000")
        assert original is not None
        repo.save_bundle(
            uow,
            replace(original, state="sent", sent_at=AT, arrival_due_at=AT + timedelta(microseconds=500000)),
        )
    assert h.runtime.agent.run_once().arrived == 0
    h.clock.at += timedelta(microseconds=500000)
    assert h.runtime.agent.run_once().arrived == 1


@pytest.mark.trace("R48", "R61", "E29-04")
def test_queue_sql_limit_preserves_order_across_fractional_seconds(tmp_path: Path) -> None:
    from crew_health.groundlink.application.ports import OutboxItem

    h = Harness(tmp_path)
    with h.db.begin(actor=Actor("engine", "test"), correlation_id="cor_1") as uow:
        for i in range(20):
            h.outbox.enqueue(
                uow,
                OutboxItem(
                    "ground_message", f"fraction_{i}", "a" * 64, AT + timedelta(microseconds=500000), True
                ),
            )
        h.outbox.enqueue(uow, OutboxItem("ground_message", "first", "a" * 64, AT, True))
    h.runtime.agent.run_once()
    assert h.owner.calls[0][1] == "first"


@pytest.mark.trace("R61")
def test_last_gap_start_survives_history_read_bound(tmp_path: Path) -> None:
    from dataclasses import replace

    from crew_health.groundlink.adapters.sqlite_repository import SqliteGroundLinkRepository

    data = profiles()
    lunar_window(data)
    h = Harness(tmp_path, environment="lunar_surface", data=data)
    deps = h.runtime.services.deps
    deps.repository = SqliteGroundLinkRepository(replace(deps.settings, history_rows=1))
    h.runtime.agent.run_once()
    h.clock.at += timedelta(hours=1)
    with h.db.begin(actor=Actor("engine", "test"), correlation_id="cor_1") as uow:
        link = h.query.current(uow)
        assert link is not None
        deps.repository.append(uow, "observed", uow.now, link)
    with h.db.snapshot() as view:
        gap = h.query.last_gap(view)
        assert gap is not None and gap.started_at == AT


@pytest.mark.trace("R48", "R61", "E29-04")
def test_durable_subscriber_enqueues_only_crew_queued_approved_hash(tmp_path: Path) -> None:
    from crew_health.groundlink.application.ports import OutboxItem
    from crew_health.groundlink.domain.errors import BundleConflict

    h = Harness(tmp_path)
    commands = h.runtime.services.commands
    payload: dict[str, object] = {
        "ground_message_id": "msg_approved",
        "text_sha256": "a" * 64,
        "queued_at": "2026-11-14T00:00:00Z",
        "simulated": True,
        "state": "queued",
    }
    with h.db.begin(actor=Actor("engine", "test"), correlation_id="cor_1") as uow:
        commands.on_ground_message_changed(uow, event(EventType.GROUND_MESSAGE_CHANGED, payload))
        commands.on_ground_message_changed(uow, event(EventType.GROUND_MESSAGE_CHANGED, payload))
        commands.on_ground_message_changed(
            uow,
            event(
                EventType.GROUND_MESSAGE_CHANGED,
                {**payload, "ground_message_id": "ground_only"},
                scope="ground",
            ),
        )
        commands.on_ground_message_changed(
            uow, event(EventType.GROUND_MESSAGE_CHANGED, {**payload, "state": "sent"})
        )
    assert h.status("msg_approved").state == "queued"
    with h.db.snapshot() as view:
        assert h.outbox.status(view, "ground_message", "ground_only") is None
    with (
        h.db.begin(actor=Actor("engine", "test"), correlation_id="cor_2") as uow,
        pytest.raises(BundleConflict),
    ):
        h.outbox.enqueue(uow, OutboxItem("ground_message", "msg_approved", "b" * 64, AT, True))
    assert not h.events


@pytest.mark.trace("R61", "V95", "EI5")
def test_inbound_receipt_and_reply_retry_without_losing_or_duplicating_records(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.owner.fail_after_commit = True
    h.inbox.receive(
        InboundItem("receipt_1", "ground_message", "msg_1", "receipt", AT, None, Actor("ground", "ground_1"))
    )
    assert h.runtime.agent.run_once().inbound == 0
    h.clock.at += timedelta(seconds=10)
    assert h.runtime.agent.run_once().inbound == 0
    h.clock.at += timedelta(seconds=1)
    assert h.runtime.agent.run_once().inbound == 1
    h.runtime.agent.run_once()
    calls = [c for c in h.owner.calls if c[0] == "receipt"]
    assert len(calls) == 2 and calls[0][2] == calls[1][2]
    assert len(h.owner.seen) == 1


@pytest.mark.trace("R48", "R61", "E29-04")
def test_queue_state_rolls_back_on_failed_transaction_and_never_allows_delete(tmp_path: Path) -> None:
    from crew_health.groundlink.application.ports import OutboxItem

    h = Harness(tmp_path)
    with (
        pytest.raises(RuntimeError),
        h.db.begin(actor=Actor("engine", "test"), correlation_id="cor_1") as uow,
    ):
        h.outbox.enqueue(uow, OutboxItem("ground_message", "rolled_back", "a" * 64, AT, True))
        raise RuntimeError("power loss before commit")
    with h.db.snapshot() as view:
        assert h.outbox.status(view, "ground_message", "rolled_back") is None
    h.enqueue()
    with (
        pytest.raises(ProblemError),
        h.db.begin(actor=Actor("engine", "test"), correlation_id="cor_2") as uow,
    ):
        uow.connection.execute("DELETE FROM groundlink_bundle")
    assert h.status().state == "queued"


@pytest.mark.trace("R49", "E29-01", "D33")
def test_invalid_demo_fields_have_safe_field_paths(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    response = h.client.post(
        "/v1/demo/ground-link",
        json={"status": "up", "gap_kind": "equipment"},
        headers={"Idempotency-Key": "command_0001"},
    )
    assert response.status_code == 422
    assert response.json()["field_errors"] == [
        {"field": "/gap_kind", "code": "invalid", "message": "INVALID_VALUE"}
    ]


@pytest.mark.trace("R49", "E29-01", "D33")
@pytest.mark.parametrize(
    "body",
    [
        {"status": "up", "gap_kind": None},
        {"status": "up", "gap_planned": None},
        {"use_schedule": 1},
    ],
)
def test_demo_request_obeys_schema_nullability_and_literal_types(
    tmp_path: Path, body: dict[str, object]
) -> None:
    h = Harness(tmp_path)
    response = h.client.post("/v1/demo/ground-link", json=body, headers={"Idempotency-Key": "command_0001"})
    assert response.status_code == 400 and response.json()["code"] == "MALFORMED_REQUEST"


@pytest.mark.trace("R48", "R61", "E29-04", "EI1")
def test_absent_owner_retries_past_exponent_ceiling_without_loss(tmp_path: Path) -> None:
    h = Harness(tmp_path, owner=False)
    h.enqueue()
    for _ in range(12):
        h.runtime.agent.run_once()
        h.clock.at += timedelta(hours=1)
    assert h.status().state == "sending" and h.status().attempts == 12
    assert h.runtime.loop is None


@pytest.mark.trace("R48", "R61", "E29-04")
def test_handoff_uses_simulated_queue_without_optional_help_owner(tmp_path: Path) -> None:
    from crew_health.groundlink.application.ports import OutboxItem

    h = Harness(tmp_path, owner=False)
    with h.db.begin(actor=Actor("engine", "test"), correlation_id="cor_1") as uow:
        h.outbox.enqueue(uow, OutboxItem("handoff", "handoff_1", "a" * 64, AT, True))
    assert h.runtime.agent.run_once().sent == 1
    h.clock.at += timedelta(seconds=2)
    assert h.runtime.agent.run_once().arrived == 1
    with h.db.snapshot() as view:
        item = h.outbox.status(view, "handoff", "handoff_1")
        assert item is not None and item.state == "arrived"
    assert not h.owner.calls


@pytest.mark.trace("R48", "EI5")
def test_owner_error_code_is_safe_and_retry_completes(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.enqueue()
    h.owner.fail = True
    assert h.runtime.agent.run_once().sent == 0
    with h.db.snapshot() as view:
        item = h.runtime.services.deps.repository.bundle(view, "ground_message", "msg_0000")
        assert item is not None and item.last_error == "owner_failed"
    h.owner.fail = False
    h.clock.at += timedelta(seconds=1)
    assert h.runtime.agent.run_once().sent == 1


@pytest.mark.trace("R49", "E29-01", "R61")
def test_late_environment_event_cannot_replace_newer_activation(tmp_path: Path) -> None:
    from dataclasses import replace

    h = Harness(tmp_path, mission=False)
    h.clock.at += timedelta(hours=1)
    newer = replace(
        event(EventType.ENVIRONMENT_ACTIVATED, {"environment_id": "mars_surface"}), occurred_at=h.clock.at
    )
    older = event(EventType.ENVIRONMENT_ACTIVATED, {"environment_id": "station_leo"})
    with h.db.begin(actor=Actor("engine", "test"), correlation_id="cor_new") as uow:
        h.runtime.services.commands.on_environment_activated(uow, newer)
    with h.db.begin(actor=Actor("engine", "test"), correlation_id="cor_old") as uow:
        h.runtime.services.commands.on_environment_activated(uow, older)
    assert h.client.get("/v1/ground-link").json()["environment_id"] == "mars_surface"
    assert len(h.events) == 1
