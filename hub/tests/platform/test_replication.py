"""T-E01-18: projection privacy, durable cursors, revocation and custody fencing."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.commands import canonical_json, command_identity
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ownership import OwnedResource, Transfer
from crew_health.kernel.replication import (
    ProjectionBody,
    ProjectionDependency,
    ProjectionKey,
    ProjectionRegistration,
    Recipient,
)
from crew_health.kernel.testing import MemoryIds
from crew_health.kernel.uow import Actor, Audience, AudienceProjectionKey, ReplicationChange
from crew_health.platform.adapters.ownership import OwnershipRepository
from crew_health.platform.application.ownership import OwnershipHooks, OwnershipService
from crew_health.platform.application.replication import ReplicaService
from crew_health.platform.domain.replication import delivery_hash
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-18", "R25", "SYSTEM-5.7.1", "SYSTEM-8.5.1")]


def peers(tmp_path):
    source = database(tmp_path / "source")
    with source.snapshot() as view:
        release = view.release
    destination = SQLiteDatabase(
        tmp_path / "destination" / "hub.sqlite",
        clock=source.clock,
        ids=MemoryIds(identifiers=tuple("destination_" + str(i) for i in range(10000))),
        release=release,
        settings=source.settings,
        origin_node_id="node_2",
        origin_incarnation="incarnation_2",
        initialize=True,
    )
    for peer in (source, destination):
        peer.migrate(
            Path("hub/src/crew_health/platform/adapters/migrations/0003_platform.sql").read_text(
                encoding="utf-8"
            )
        )
        peer.migrate(
            "CREATE TABLE test_replica(stream_id TEXT NOT NULL,seq INTEGER NOT NULL,object_ref TEXT NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(stream_id,seq)) STRICT;"
        )
    return source, destination


def projection_schema():
    # Resolve the guarded allowed resource leaf without introducing another accepted field or type.
    common = json.loads(Path("contracts/api/schemas/common.schema.json").read_text())
    identifier = common["$defs"]["id"]
    schema = {
        "type": "object",
        "properties": {"observation_id": identifier, "episode_id": {"anyOf": [identifier, {"type": "null"}]}},
        "required": ["observation_id", "episode_id"],
        "additionalProperties": False,
    }
    return canonical_json(schema)


def projected(observation_id, *, episode_id=None, dependencies=()):
    return ProjectionBody(
        ProjectionKey("records", "observation", 1),
        observation_id,
        ("/episode_id", "/observation_id"),
        canonical_json({"observation_id": observation_id, "episode_id": episode_id}),
        dependencies,
    )


def exchange(
    db,
    *,
    bodies=None,
    grant=None,
    fields=None,
    dependencies=None,
    apply_failure=None,
    schema=None,
    allowlist=None,
    authorize_revocation=None,
):
    bodies = {} if bodies is None else bodies
    grant = [True] if grant is None else grant
    dependencies = set() if dependencies is None else dependencies
    fields = {"/episode_id", "/observation_id"} if fields is None else fields

    class Provider:
        def export(self, view, change, recipient):
            return bodies.get(change.record_id)

    class Applier:
        def apply(self, uow, delivery, recipient):
            uow.connection.execute(
                "INSERT INTO test_replica VALUES(?,?,?,?)",
                (
                    delivery.stream_id,
                    delivery.delivery_seq,
                    delivery.body.object_ref,
                    delivery.body.data_json,
                ),
            )
            if apply_failure is not None and apply_failure[0]:
                raise OSError("injected apply failure after effect")

    schema = projection_schema() if schema is None else schema
    key = ProjectionKey("records", "observation", 1)
    registration = ProjectionRegistration(
        key, schema, hashlib.sha256(schema).hexdigest(), Provider(), Applier()
    )

    def sign(raw):
        return hmac.new(b"explicit hermetic transport key", raw, hashlib.sha256).digest()

    def verify(control):
        unsigned = service.controls.dump_json(replace(control, signature=b""))
        return (
            control.authority_ref == "node_1"
            and control.recipient_id == "peer_2"
            and hmac.compare_digest(control.signature, sign(unsigned))
        )

    service = ReplicaService(
        registrations=(registration,),
        allowed_fields={
            key: frozenset({"/episode_id", "/observation_id"}) if allowlist is None else allowlist
        },
        settings=db.settings,
        ids=db.ids,
        node_id=db.origin_node_id,
        incarnation=db.origin_incarnation,
        authorize=lambda view, recipient, body: (
            grant[0]
            and recipient.recipient_id == "peer_2"
            and (body is None or set(body.selected_fields) <= fields)
        ),
        secret=lambda recipient: hashlib.sha256(
            ("explicit secret:" + recipient.recipient_id + ":" + str(recipient.policy_epoch)).encode()
        ).digest(),
        sign=sign,
        verify=verify,
        erase=lambda uow, stream: uow.connection.execute(
            "DELETE FROM test_replica WHERE stream_id=?", (stream,)
        ),
        dependency=lambda uow, ref: ref in dependencies,
        authorize_revocation=authorize_revocation,
    )
    return service


def recipient(epoch=1):
    return Recipient("peer_2", "subject-consented-onboard", epoch, frozenset({Role.CREW}), ("grant_1",))


def append_changes(db, *changes):
    with db.begin(actor=Actor("operator", "operator"), correlation_id="append") as uow:
        for record_id, exportable in changes:
            uow.replicate(
                ReplicationChange(
                    ProjectionKey("records", "observation", 1) if exportable else None,
                    "observation",
                    record_id,
                    "create",
                    canonical_json({"record_id": record_id}),
                    (),
                    "release_1",
                    Audience("owner", record_id, AudienceProjectionKey("records", "observation", 1), "1"),
                )
            )


def stream_id(db, who):
    with db.snapshot() as view:
        rows = view.connection.rows(
            "SELECT stream_id FROM platform_replica_stream WHERE recipient_id=? AND purpose=? AND policy_epoch=?",
            (who.recipient_id, who.purpose, who.policy_epoch),
            limit=1,
        )
    return rows[0][0]


def materialized(db, service, who):
    with db.begin(actor=Actor("operator", "operator"), correlation_id="materialize") as uow:
        count = service.materialize(uow, who, limit=100)
    stream = stream_id(db, who)
    with db.snapshot() as view:
        deliveries = service.page(view, who, stream_id=stream, after=0, limit=100)
    return count, deliveries


def test_filtered_materialization_allocates_contiguous_deliveries_and_commits_scan_cursor(tmp_path):
    source, _ = peers(tmp_path)
    service = exchange(source, bodies={"obs_1": projected("obs_1"), "obs_2": projected("obs_2")})
    append_changes(source, ("private_local", False), ("obs_1", True), ("not_granted", True), ("obs_2", True))
    count, deliveries = materialized(source, service, recipient())
    assert count == 2 and tuple(item.delivery_seq for item in deliveries) == (1, 2)
    assert tuple(item.body.object_ref for item in deliveries) == ("obs_1", "obs_2")
    assert all(item.origin_token.isalnum() and len(item.origin_token) == 64 for item in deliveries)
    serialized = service.deliveries.dump_json(deliveries[0])
    assert b"origin_seq" not in serialized and b"private_local" not in serialized
    count, again = materialized(source, service, recipient())
    assert count == 0 and again == deliveries
    with source.snapshot() as view:
        assert view.connection.rows(
            "SELECT scan_seq,delivery_seq FROM platform_replica_stream", (), limit=1
        ) == ((4, 2),)


def test_apply_effect_and_contiguous_cursor_are_atomic_and_duplicate_hash_is_checked(tmp_path):
    source, dest = peers(tmp_path)
    sender = exchange(source, bodies={"obs_1": projected("obs_1")})
    append_changes(source, ("obs_1", True))
    _, (delivery,) = materialized(source, sender, recipient())
    failing = [True]
    receiver = exchange(dest, apply_failure=failing)
    with pytest.raises(OSError):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="apply") as uow:
            receiver.apply(uow, recipient(), delivery)
    with dest.snapshot() as view:
        assert not view.connection.rows("SELECT seq FROM test_replica", (), limit=1)
        assert not view.connection.rows("SELECT seq FROM platform_replica_applied", (), limit=1)
    failing[0] = False
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="retry") as uow:
        assert receiver.apply(uow, recipient(), delivery) == 1
        assert receiver.apply(uow, recipient(), delivery) == 1
        changed = replace(delivery, body=projected("obs_changed"))
        changed = replace(changed, envelope_sha256=delivery_hash(changed))
        with pytest.raises(ProblemError):
            receiver.apply(uow, recipient(), changed)
    with dest.snapshot() as view:
        assert view.connection.rows("SELECT COUNT(*) FROM test_replica", (), limit=1) == ((1,),)


def test_missing_required_dependency_and_out_of_order_delivery_cannot_advance_cursor(tmp_path):
    source, dest = peers(tmp_path)
    dependency = ProjectionDependency("/episode_id", "episode_1", "required")
    sender = exchange(
        source,
        bodies={
            "obs_1": projected("obs_1", episode_id="episode_1", dependencies=(dependency,)),
            "obs_2": projected("obs_2"),
        },
    )
    append_changes(source, ("obs_1", True), ("obs_2", True))
    _, deliveries = materialized(source, sender, recipient())
    available = set()
    receiver = exchange(dest, dependencies=available)
    for item in (deliveries[0], deliveries[1]):
        with pytest.raises(ProblemError):
            with dest.begin(actor=Actor("operator", "operator"), correlation_id="missing") as uow:
                receiver.apply(uow, recipient(), item)
    available.add("episode_1")
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="repaired") as uow:
        assert receiver.apply(uow, recipient(), deliveries[0]) == 1
        assert receiver.apply(uow, recipient(), deliveries[1]) == 2


@pytest.mark.parametrize(
    "body",
    [
        projected("obs_1", dependencies=(ProjectionDependency("/episode_id", "forbidden", "omitted"),)),
        replace(projected("obs_1"), selected_fields=("/observation_id",)),
        replace(
            projected("obs_1"),
            data_json=b'{"episode_id":null,"observation_id":"obs_1","patient_id":"forbidden"}',
        ),
    ],
)
def test_ungranted_leaf_or_forbidden_omitted_dependency_never_leaves_sender(tmp_path, body):
    source, _ = peers(tmp_path)
    sender = exchange(source, bodies={"obs_1": body})
    append_changes(source, ("obs_1", True))
    with pytest.raises(ProblemError):
        with source.begin(actor=Actor("operator", "operator"), correlation_id="invalid") as uow:
            sender.materialize(uow, recipient(), limit=1)
    with source.snapshot() as view:
        assert not view.connection.rows("SELECT seq FROM platform_replica_delivery", (), limit=1)


def test_current_grant_is_rechecked_before_send_and_apply(tmp_path):
    source, dest = peers(tmp_path)
    grant = [True]
    sender = exchange(source, bodies={"obs_1": projected("obs_1")}, grant=grant)
    receiver = exchange(dest, grant=grant)
    append_changes(source, ("obs_1", True))
    _, (delivery,) = materialized(source, sender, recipient())
    grant[0] = False
    with source.snapshot() as view:
        with pytest.raises(ProblemError):
            sender.page(view, recipient(), stream_id=delivery.stream_id, after=0, limit=1)
    with pytest.raises(ProblemError):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="denied") as uow:
            receiver.apply(uow, recipient(), delivery)


def test_signed_revocation_bypasses_blocked_data_lane_and_rejects_old_epoch(tmp_path):
    source, dest = peers(tmp_path)
    grant = [True]
    sender = exchange(source, bodies={"obs_1": projected("obs_1")}, grant=grant)
    receiver = exchange(dest, grant=grant)
    append_changes(source, ("obs_1", True))
    _, (delivery,) = materialized(source, sender, recipient())
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="apply") as uow:
        receiver.apply(uow, recipient(), delivery)
    with source.begin(actor=Actor("operator", "operator"), correlation_id="revoke") as uow:
        control = sender.revoke(uow, recipient(), stream_id=delivery.stream_id)
    grant[0] = False
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="control") as uow:
        assert receiver.apply_revocation(uow, control) == control.control_id
        assert receiver.apply_revocation(uow, control) == control.control_id
    with dest.snapshot() as view:
        assert not view.connection.rows("SELECT seq FROM test_replica", (), limit=1)
    grant[0] = True
    with pytest.raises(ProblemError):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="old_epoch") as uow:
            receiver.apply(uow, recipient(), delivery)
    with source.begin(actor=Actor("operator", "operator"), correlation_id="ack_control") as uow:
        sender.acknowledge_revocation(uow, control_id=control.control_id, recipient_id=control.recipient_id)
    with source.snapshot() as view:
        assert view.connection.rows("SELECT acknowledged FROM platform_revocation", (), limit=1) == ((1,),)
    forged = replace(control, control_id="forged", signature=b"wrong")
    with pytest.raises(ProblemError):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="forged") as uow:
            receiver.apply_revocation(uow, forged)


def test_revoke_retry_returns_same_durable_control_after_fencing(tmp_path):
    source, _ = peers(tmp_path)
    service = exchange(source)
    materialized(source, service, recipient())
    stream = stream_id(source, recipient())
    with source.begin(actor=Actor("operator", "operator"), correlation_id="first") as uow:
        first = service.revoke(uow, recipient(), stream_id=stream)
    with source.begin(actor=Actor("operator", "operator"), correlation_id="retry") as uow:
        assert service.revoke(uow, recipient(), stream_id=stream) == first


def ownership(db):
    db.migrate("CREATE TABLE test_custody(resource_id TEXT PRIMARY KEY,active INTEGER NOT NULL) STRICT;")
    snapshot = b"operational synthetic resource snapshot"

    def validate(uow, transfer):
        if transfer.snapshot_sha256 != hashlib.sha256(snapshot).hexdigest():
            raise ProblemError(ErrorCode.INVALID_VALUE, "INVALID_VALUE")

    def install(uow, transfer):
        uow.connection.execute("INSERT INTO test_custody VALUES(?,1)", (transfer.source.resource_id,))

    def fence(uow, transfer):
        uow.connection.execute(
            "UPDATE test_custody SET active=0 WHERE resource_id=?", (transfer.source.resource_id,)
        )

    def sign(raw):
        return hmac.new(db.origin_node_id.encode(), raw, hashlib.sha256).digest()

    def verify(raw, signature, issuer):
        return hmac.compare_digest(signature, hmac.new(issuer.encode(), raw, hashlib.sha256).digest())

    service = OwnershipService(
        node_id=db.origin_node_id,
        incarnation=db.origin_incarnation,
        ids=db.ids,
        hooks={"routine": OwnershipHooks(validate, install, fence)},
        repository=OwnershipRepository(),
        sign=sign,
        verify=verify,
    )
    return service, snapshot


def transfer_fixture(tmp_path):
    source, dest = peers(tmp_path)
    source_owner, snapshot = ownership(source)
    dest_owner, _ = ownership(dest)
    resource = OwnedResource("routine", "resource_1", source.origin_node_id, source.origin_incarnation, 1, 1)
    identity = command_identity(
        key="transfer_key",
        actor_ref="operator",
        operation="custody.transfer",
        target="/resource/resource_1",
        parameters_json=b"{}",
        body_fingerprint="0" * 64,
        fingerprint_kind="sha256",
    )
    transfer = Transfer(
        "transfer_1",
        resource,
        dest.origin_node_id,
        dest.origin_incarnation,
        2,
        "snapshot_1",
        hashlib.sha256(snapshot).hexdigest(),
        identity,
    )
    with source.begin(actor=Actor("operator", "operator"), correlation_id="seed") as uow:
        source_owner.seed(uow, resource)
        uow.connection.execute("INSERT INTO test_custody VALUES(?,1)", (resource.resource_id,))
    return source, dest, source_owner, dest_owner, resource, transfer


def test_ownership_transfer_phases_retries_and_fencing_allow_at_most_one_writer(tmp_path):
    source, dest, old, new, resource, transfer = transfer_fixture(tmp_path)
    new_resource = replace(
        resource, owner_node_id=dest.origin_node_id, owner_incarnation=dest.origin_incarnation, epoch=2
    )
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="prepare") as uow:
        prepared = new.prepare(uow, transfer)
        assert new.prepare(uow, transfer) == prepared
        with pytest.raises(ProblemError):
            new.require(uow, new_resource)
    with source.begin(actor=Actor("operator", "operator"), correlation_id="fence") as uow:
        old.require(uow, resource)
        fenced = old.fence(uow, transfer, prepared)
        assert old.fence(uow, transfer, prepared) == fenced
        with pytest.raises(ProblemError):
            old.require(uow, resource)
        with pytest.raises(ProblemError):
            old.cancel(uow, transfer)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="accept") as uow:
        accepted = new.accept(uow, transfer, fenced)
        assert new.accept(uow, transfer, fenced) == accepted
        new.require(uow, new_resource)
    with source.begin(actor=Actor("operator", "operator"), correlation_id="complete") as uow:
        completed = old.complete(uow, transfer, accepted)
        assert old.complete(uow, transfer, accepted) == completed
        with pytest.raises(ProblemError):
            old.require(uow, resource)
    with source.snapshot() as view:
        assert view.connection.rows("SELECT active FROM test_custody", (), limit=1) == ((0,),)
    with dest.snapshot() as view:
        assert view.connection.rows("SELECT active FROM test_custody", (), limit=1) == ((1,),)


def test_ownership_cancel_before_fence_is_durable_on_both_sides(tmp_path):
    source, dest, old, new, resource, transfer = transfer_fixture(tmp_path)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="prepare") as uow:
        prepared = new.prepare(uow, transfer)
    with source.begin(actor=Actor("operator", "operator"), correlation_id="cancel_source") as uow:
        assert old.cancel(uow, transfer).phase == "cancelled"
        old.require(uow, resource)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="cancel_dest") as uow:
        assert new.cancel(uow, transfer).phase == "cancelled"
    with pytest.raises(ProblemError):
        with source.begin(actor=Actor("operator", "operator"), correlation_id="late_fence") as uow:
            old.fence(uow, transfer, prepared)


def test_ownership_rejects_competing_prepare_for_same_resource_epoch(tmp_path):
    source, dest, old, new, resource, transfer = transfer_fixture(tmp_path)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="prepare") as uow:
        new.prepare(uow, transfer)
    with pytest.raises(ProblemError):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="competing") as uow:
            new.prepare(uow, replace(transfer, transfer_id="competing_transfer"))


def test_ownership_final_revision_and_certificate_are_checked_before_fencing(tmp_path):
    source, dest, old, new, resource, transfer = transfer_fixture(tmp_path)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="prepare") as uow:
        prepared = new.prepare(uow, transfer)
    with source.begin(actor=Actor("operator", "operator"), correlation_id="changed_source") as uow:
        uow.connection.execute(
            "UPDATE platform_ownership SET head=? WHERE kind=? AND resource_id=?",
            (old.head_codec.dump_json(replace(resource, revision=2)), resource.kind, resource.resource_id),
        )
    with pytest.raises(ProblemError):
        with source.begin(actor=Actor("operator", "operator"), correlation_id="stale") as uow:
            old.fence(uow, transfer, prepared)
    with pytest.raises(ProblemError):
        with source.begin(actor=Actor("operator", "operator"), correlation_id="forged") as uow:
            old.fence(
                uow, transfer, replace(prepared, certificate_json=b'{"payload":"forged","signature":"00"}')
            )
    with source.snapshot() as view:
        assert view.connection.rows("SELECT fenced FROM platform_ownership", (), limit=1) == ((0,),)


def test_materialization_failure_rolls_back_partial_deliveries_and_scan_watermark(tmp_path):
    source, _ = peers(tmp_path)
    bodies = {
        "obs_1": projected("obs_1"),
        "obs_2": replace(projected("obs_2"), selected_fields=("/observation_id",)),
    }
    sender = exchange(source, bodies=bodies)
    append_changes(source, ("obs_1", True), ("obs_2", True))
    with pytest.raises(ProblemError):
        with source.begin(actor=Actor("operator", "operator"), correlation_id="partial") as uow:
            sender.materialize(uow, recipient(), limit=100)
    with source.snapshot() as view:
        assert not view.connection.rows("SELECT seq FROM platform_replica_delivery", (), limit=10)
        assert not view.connection.rows("SELECT stream_id FROM platform_replica_stream", (), limit=10)
    bodies["obs_2"] = projected("obs_2")
    count, deliveries = materialized(source, sender, recipient())
    assert count == 2 and tuple(delivery.delivery_seq for delivery in deliveries) == (1, 2)


def test_acknowledgment_checks_exact_committed_hash_and_monotonic_cursor(tmp_path):
    source, _ = peers(tmp_path)
    sender = exchange(source, bodies={"obs_1": projected("obs_1"), "obs_2": projected("obs_2")})
    append_changes(source, ("obs_1", True), ("obs_2", True))
    _, deliveries = materialized(source, sender, recipient())
    with source.begin(actor=Actor("operator", "operator"), correlation_id="ack") as uow:
        with pytest.raises(ProblemError):
            sender.acknowledge(
                uow, recipient(), stream_id=deliveries[0].stream_id, through=1, envelope_sha256="f" * 64
            )
        sender.acknowledge(
            uow,
            recipient(),
            stream_id=deliveries[1].stream_id,
            through=2,
            envelope_sha256=deliveries[1].envelope_sha256,
        )
        sender.acknowledge(
            uow,
            recipient(),
            stream_id=deliveries[1].stream_id,
            through=2,
            envelope_sha256=deliveries[1].envelope_sha256,
        )
        with pytest.raises(ProblemError):
            sender.acknowledge(
                uow,
                recipient(),
                stream_id=deliveries[0].stream_id,
                through=1,
                envelope_sha256=deliveries[0].envelope_sha256,
            )
    with source.snapshot() as view:
        assert view.connection.rows("SELECT ack_seq FROM platform_replica_stream", (), limit=1) == ((2,),)


def test_omitted_dependency_stays_null_without_a_hidden_identifier(tmp_path):
    source, dest = peers(tmp_path)
    omitted = ProjectionDependency("/episode_id", None, "omitted")
    sender = exchange(source, bodies={"obs_1": projected("obs_1", dependencies=(omitted,))})
    receiver = exchange(dest)
    append_changes(source, ("obs_1", True))
    _, (delivery,) = materialized(source, sender, recipient())
    assert delivery.body.dependencies == (omitted,)
    assert json.loads(delivery.body.data_json)["episode_id"] is None
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="without_hidden_dep") as uow:
        assert receiver.apply(uow, recipient(), delivery) == 1


def test_policy_epoch_changes_stream_and_origin_token_without_exposing_skipped_positions(tmp_path):
    source, _ = peers(tmp_path)
    sender = exchange(source, bodies={"obs_1": projected("obs_1")})
    append_changes(source, ("hidden_1", False), ("obs_1", True))
    _, (first,) = materialized(source, sender, recipient(1))
    _, (second,) = materialized(source, sender, recipient(2))
    assert first.stream_id != second.stream_id
    assert first.origin_token != second.origin_token
    assert first.delivery_seq == second.delivery_seq == 1
    assert first.body == second.body


def test_accept_never_overwrites_a_newer_durable_destination_epoch(tmp_path):
    source, dest, old, new, resource, transfer = transfer_fixture(tmp_path)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="prepare") as uow:
        prepared = new.prepare(uow, transfer)
    with source.begin(actor=Actor("operator", "operator"), correlation_id="fence") as uow:
        fenced = old.fence(uow, transfer, prepared)
    newer = replace(
        resource,
        owner_node_id=dest.origin_node_id,
        owner_incarnation=dest.origin_incarnation,
        epoch=3,
        revision=4,
    )
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="newer_recovery_head") as uow:
        new.seed(uow, newer)
    with pytest.raises(ProblemError):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="stale_certificate") as uow:
            new.accept(uow, transfer, fenced)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="still_newer") as uow:
        new.require(uow, newer)


def test_crash_during_source_fence_preserves_primary_owner_and_timer_head(tmp_path):
    source, dest, old, new, resource, transfer = transfer_fixture(tmp_path)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="prepare") as uow:
        prepared = new.prepare(uow, transfer)
    original = old.hooks[resource.kind]

    def failing_fence(uow, value):
        original.fence(uow, value)
        raise OSError("crash after timer fence before certificate")

    old.hooks[resource.kind] = replace(original, fence=failing_fence)
    with pytest.raises(OSError):
        with source.begin(actor=Actor("operator", "operator"), correlation_id="failed_fence") as uow:
            old.fence(uow, transfer, prepared)
    with source.begin(actor=Actor("operator", "operator"), correlation_id="primary_available") as uow:
        old.require(uow, resource)
        assert uow.connection.rows("SELECT active FROM test_custody", (), limit=1) == ((1,),)
    with source.snapshot() as view:
        assert not view.connection.rows("SELECT transfer_id FROM platform_transfer", (), limit=1)


def test_crash_during_destination_install_preserves_handover_gap_and_retry_identity(tmp_path):
    source, dest, old, new, resource, transfer = transfer_fixture(tmp_path)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="prepare") as uow:
        prepared = new.prepare(uow, transfer)
    with source.begin(actor=Actor("operator", "operator"), correlation_id="fence") as uow:
        fenced = old.fence(uow, transfer, prepared)
    original = new.hooks[resource.kind]

    def failing_install(uow, value):
        original.install(uow, value)
        raise OSError("crash after resource install before head")

    new.hooks[resource.kind] = replace(original, install=failing_install)
    with pytest.raises(OSError):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="failed_install") as uow:
            new.accept(uow, transfer, fenced)
    with dest.snapshot() as view:
        assert not view.connection.rows("SELECT active FROM test_custody", (), limit=1)
        assert new.status(view, transfer.transfer_id) == prepared
    with source.begin(actor=Actor("operator", "operator"), correlation_id="source_stays_fenced") as uow:
        with pytest.raises(ProblemError):
            old.require(uow, resource)
    new.hooks[resource.kind] = original
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="install_retry") as uow:
        accepted = new.accept(uow, transfer, fenced)
        assert accepted.phase == "accepted"
        assert uow.connection.rows("SELECT active FROM test_custody", (), limit=1) == ((1,),)


def test_field_grant_narrowing_suppresses_fixed_projection_before_send_and_apply(tmp_path):
    source, dest = peers(tmp_path)
    fields = {"/episode_id", "/observation_id"}
    sender = exchange(source, bodies={"obs_1": projected("obs_1")}, fields=fields)
    receiver = exchange(dest, fields=fields)
    append_changes(source, ("obs_1", True))
    _, (delivery,) = materialized(source, sender, recipient())
    fields.remove("/episode_id")
    with source.snapshot() as view:
        with pytest.raises(ProblemError):
            sender.page(view, recipient(), stream_id=delivery.stream_id, after=0, limit=1)
    with pytest.raises(ProblemError):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="narrowed") as uow:
            receiver.apply(uow, recipient(), delivery)
    with dest.snapshot() as view:
        assert not view.connection.rows("SELECT seq FROM test_replica", (), limit=1)


def test_cancelled_proposal_epoch_is_never_reused_and_later_monotonic_epoch_can_proceed(tmp_path):
    source, dest, old, new, resource, transfer = transfer_fixture(tmp_path)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="prepare_two") as uow:
        new.prepare(uow, transfer)
    with source.begin(actor=Actor("operator", "operator"), correlation_id="cancel_two_source") as uow:
        old.cancel(uow, transfer)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="cancel_two_dest") as uow:
        new.cancel(uow, transfer)
    reused = replace(
        transfer, transfer_id="reused_two", command=replace(transfer.command, key="command_reuse")
    )
    with pytest.raises(ProblemError):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="reject_reused_epoch") as uow:
            new.prepare(uow, reused)
    later = replace(
        transfer,
        transfer_id="transfer_three",
        new_epoch=3,
        command=replace(transfer.command, key="command_three", body_fingerprint="1" * 64),
    )
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="prepare_three") as uow:
        prepared = new.prepare(uow, later)
        assert prepared.phase == "prepared"
    with source.begin(actor=Actor("operator", "operator"), correlation_id="fence_three") as uow:
        old.require(uow, resource)
        fenced = old.fence(uow, later, prepared)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="accept_three") as uow:
        assert new.accept(uow, later, fenced).phase == "accepted"
        new.require(
            uow,
            replace(
                resource,
                owner_node_id=dest.origin_node_id,
                owner_incarnation=dest.origin_incarnation,
                epoch=3,
            ),
        )
