"""T-E01-18: independent stream identities, control authority and closed catalog."""

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor
from crew_health.platform.domain.replication import delivery_hash

from .test_replication import (
    append_changes,
    exchange,
    materialized,
    peers,
    projected,
    recipient,
)

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-18", "SYSTEM-5.7.1")]


def deliveries_for(source):
    sender = exchange(source, bodies={"obs_1": projected("obs_1")})
    append_changes(source, ("obs_1", True))
    _, (delivery,) = materialized(source, sender, recipient())
    return sender, delivery


def test_two_senders_to_one_recipient_both_apply_sequence_one(tmp_path):
    first, dest = peers(tmp_path / "first")
    second, _ = peers(tmp_path / "second")
    second.origin_node_id = "node_3"
    second.origin_incarnation = "incarnation_3"
    _, from_first = deliveries_for(first)
    _, from_second = deliveries_for(second)
    assert from_first.stream_id != from_second.stream_id
    receiver = exchange(dest)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="apply") as uow:
        assert receiver.apply(uow, recipient(), from_first) == 1
        assert receiver.apply(uow, recipient(), from_second) == 1
    with dest.snapshot() as view:
        assert view.connection.rows("SELECT COUNT(*) FROM test_replica", (), limit=1) == ((2,),)


def test_origin_projection_replay_is_noop_but_advances_delivery_cursor(tmp_path):
    source, dest = peers(tmp_path)
    _, delivery = deliveries_for(source)
    receiver = exchange(dest)
    replay = replace(delivery, delivery_seq=2)
    replay = replace(replay, envelope_sha256=delivery_hash(replay))
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="apply") as uow:
        assert receiver.apply(uow, recipient(), delivery) == 1
        assert receiver.apply(uow, recipient(), replay) == 2
    with dest.snapshot() as view:
        assert view.connection.rows("SELECT COUNT(*) FROM test_replica", (), limit=1) == ((1,),)
        assert view.connection.rows("SELECT MAX(seq) FROM platform_replica_applied", (), limit=1) == ((2,),)


def test_same_origin_with_changed_projection_is_applied(tmp_path):
    source, dest = peers(tmp_path)
    _, delivery = deliveries_for(source)
    changed = replace(delivery, delivery_seq=2, body=projected("obs_2"))
    changed = replace(changed, envelope_sha256=delivery_hash(changed))
    receiver = exchange(dest)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="apply") as uow:
        receiver.apply(uow, recipient(), delivery)
        receiver.apply(uow, recipient(), changed)
    with dest.snapshot() as view:
        assert view.connection.rows("SELECT COUNT(*) FROM test_replica", (), limit=1) == ((2,),)


def test_stream_source_cannot_change_after_first_delivery(tmp_path):
    source, dest = peers(tmp_path)
    _, delivery = deliveries_for(source)
    receiver = exchange(dest)
    forged = replace(delivery, delivery_seq=2, source_node_id="other_node")
    forged = replace(forged, envelope_sha256=delivery_hash(forged))
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="apply") as uow:
        receiver.apply(uow, recipient(), delivery)
    with pytest.raises(ProblemError):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="forge") as uow:
            receiver.apply(uow, recipient(), forged)


def test_revocation_succeeds_after_data_grant_withdrawal(tmp_path):
    source, _ = peers(tmp_path)
    grant = [True]
    sender = exchange(source, grant=grant)
    materialized(source, sender, recipient())
    stream = sender._stream(recipient())
    grant[0] = False
    with source.begin(actor=Actor("operator", "operator"), correlation_id="revoke") as uow:
        control = sender.revoke(uow, recipient(), stream_id=stream)
        assert sender.revoke(uow, recipient(), stream_id=stream) == control
    with source.snapshot() as view:
        assert view.connection.rows("SELECT fenced FROM platform_replica_stream", (), limit=1) == ((1,),)
        assert view.connection.rows("SELECT control_id FROM platform_revocation", (), limit=1) == (
            (control.control_id,),
        )


def test_untrusted_actor_cannot_revoke_enrolled_stream(tmp_path):
    source, _ = peers(tmp_path)
    sender = exchange(source)
    materialized(source, sender, recipient())
    with pytest.raises(ProblemError):
        with source.begin(actor=Actor("assistant", "assistant"), correlation_id="revoke") as uow:
            sender.revoke(uow, recipient(), stream_id=sender._stream(recipient()))


def test_journal_is_refused_by_import_even_if_caller_widens_allowlist(tmp_path):
    source, _ = peers(tmp_path)
    service = exchange(source)
    body = replace(
        projected("obs_1"), selected_fields=("/journal",), data_json=canonical_json({"journal": "private"})
    )
    service.allowed_fields[body.key] = frozenset({"/journal"})
    with pytest.raises(ProblemError) as caught:
        service._body(body)
    assert caught.value.code.value == "UNAUTHORIZED_SCOPE"


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=30))
def test_sender_identity_participates_in_stream_id(node):
    from crew_health.kernel.testing import MemoryIds
    from crew_health.platform.application.replication import ReplicaService

    service = ReplicaService.__new__(ReplicaService)
    service.ids = MemoryIds(identifiers=())
    service.node_id, service.incarnation = node, "incarnation_1"
    original = service._stream(recipient())
    service.node_id = node + "x"
    assert service._stream(recipient()) != original
    service.node_id, service.incarnation = node, "incarnation_2"
    assert service._stream(recipient()) != original


def test_caller_cannot_register_journal_in_observation_catalog(tmp_path):
    source, _ = peers(tmp_path)
    schema = canonical_json(
        {"type": "object", "properties": {"journal": {"type": "string"}}, "additionalProperties": False}
    )
    with pytest.raises(ProblemError):
        exchange(source, schema=schema, allowlist=frozenset({"/journal"}))


def test_origin_projection_replay_across_incarnations_survives_receiver_restart(tmp_path):
    source, dest = peers(tmp_path)
    _, delivery = deliveries_for(source)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="first") as uow:
        exchange(dest).apply(uow, recipient(), delivery)
    restored = replace(delivery, source_incarnation="restored_incarnation")
    restored = replace(
        restored,
        stream_id=dest.ids.derived(
            "stream",
            restored.source_node_id,
            restored.source_incarnation,
            recipient().recipient_id,
            recipient().purpose,
            str(recipient().policy_epoch),
        ),
    )
    restored = replace(restored, envelope_sha256=delivery_hash(restored))
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="restore") as uow:
        assert exchange(dest).apply(uow, recipient(), restored) == 1
    with dest.snapshot() as view:
        assert view.connection.rows("SELECT COUNT(*) FROM test_replica", (), limit=1) == ((1,),)
        assert view.connection.rows("SELECT COUNT(*) FROM platform_replica_source", (), limit=1) == ((2,),)


def test_checkpoint_failure_rolls_back_projection_source_and_deduplication(tmp_path):
    from crew_health.platform.domain.errors import StorageConstraintViolated

    source, dest = peers(tmp_path)
    _, delivery = deliveries_for(source)
    receiver = exchange(dest)
    dest.migrate(
        "CREATE TRIGGER fail_checkpoint BEFORE INSERT ON platform_replica_applied BEGIN SELECT RAISE(ABORT, 'injected checkpoint failure'); END;"
    )
    with pytest.raises(StorageConstraintViolated):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="failed") as uow:
            receiver.apply(uow, recipient(), delivery)
    with dest.snapshot() as view:
        for table in (
            "test_replica",
            "platform_replica_origin",
            "platform_replica_source",
            "platform_replica_applied",
        ):
            assert view.connection.rows("SELECT COUNT(*) FROM " + table, (), limit=1) == ((0,),)
    dest.migrate("DROP TRIGGER fail_checkpoint;")
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="retry") as uow:
        assert receiver.apply(uow, recipient(), delivery) == 1


def test_enqueue_failure_rolls_back_revocation_fence(tmp_path):
    from crew_health.platform.domain.errors import StorageConstraintViolated

    source, _ = peers(tmp_path)
    sender = exchange(source)
    materialized(source, sender, recipient())
    source.migrate(
        "CREATE TRIGGER fail_revoke BEFORE INSERT ON platform_revocation BEGIN SELECT RAISE(ABORT, 'injected control failure'); END;"
    )
    with pytest.raises(StorageConstraintViolated):
        with source.begin(actor=Actor("operator", "operator"), correlation_id="failed") as uow:
            sender.revoke(uow, recipient(), stream_id=sender._stream(recipient()))
    with source.snapshot() as view:
        assert view.connection.rows("SELECT fenced FROM platform_replica_stream", (), limit=1) == ((0,),)
        assert view.connection.rows("SELECT COUNT(*) FROM platform_revocation", (), limit=1) == ((0,),)


def test_subject_revocation_uses_explicit_authority_after_grant_loss(tmp_path):
    source, _ = peers(tmp_path)
    grant = [True]
    sender = exchange(
        source, grant=grant, authorize_revocation=lambda uow, who: uow.actor == Actor("person", "subject_1")
    )
    materialized(source, sender, recipient())
    grant[0] = False
    with pytest.raises(ProblemError):
        with source.begin(actor=Actor("person", "foreign_subject"), correlation_id="denied") as uow:
            sender.revoke(uow, recipient(), stream_id=sender._stream(recipient()))
    with source.begin(actor=Actor("person", "subject_1"), correlation_id="revoke") as uow:
        sender.revoke(uow, recipient(), stream_id=sender._stream(recipient()))


def test_unknown_recipient_cannot_revoke_another_enrollment(tmp_path):
    source, _ = peers(tmp_path)
    sender = exchange(source)
    materialized(source, sender, recipient())
    foreign = replace(recipient(), recipient_id="other_peer")
    with pytest.raises(ProblemError):
        with source.begin(actor=Actor("operator", "operator"), correlation_id="denied") as uow:
            sender.revoke(uow, foreign, stream_id=sender._stream(recipient()))


def test_registration_cannot_widen_effective_leaf_type(tmp_path):
    source, _ = peers(tmp_path)
    # The effective contract requires the hub ID pattern, not an arbitrary string.
    schema = canonical_json(
        {
            "type": "object",
            "properties": {"observation_id": {"type": "string"}},
            "additionalProperties": False,
        }
    )
    with pytest.raises(ProblemError):
        exchange(source, schema=schema, allowlist=frozenset({"/observation_id"}))


def test_registration_cannot_grant_entire_nested_object(tmp_path):
    from crew_health.kernel.replication import ProjectionKey
    from crew_health.platform.application.replication import _catalog_schema
    from crew_health.platform.domain.replication import catalog_leaf_schemas

    key = ProjectionKey("records", "answer", 1)
    allowed = catalog_leaf_schemas(key, _catalog_schema(key), maximum=1024)
    assert "/actor" not in allowed
    assert "/actor/id" in allowed


def test_persisted_source_mismatch_is_rejected_after_restart(tmp_path):
    source, dest = peers(tmp_path)
    _, delivery = deliveries_for(source)
    with dest.begin(actor=Actor("operator", "operator"), correlation_id="first") as uow:
        exchange(dest).apply(uow, recipient(), delivery)
        uow.connection.execute(
            "UPDATE platform_replica_source SET source_node_id=? WHERE stream_id=?",
            ("foreign_node", delivery.stream_id),
        )
    with pytest.raises(ProblemError):
        with dest.begin(actor=Actor("operator", "operator"), correlation_id="restart") as uow:
            exchange(dest).apply(uow, recipient(), delivery)


def test_unresolved_internal_projection_stays_unavailable():
    from crew_health.kernel.replication import ProjectionKey
    from crew_health.platform.application.replication import _catalog_schema
    from crew_health.platform.domain.replication import catalog_leaf_schemas

    key = ProjectionKey("routine", "ownership", 1)
    assert catalog_leaf_schemas(key, _catalog_schema(key), maximum=1024) == {}


def test_schema_annotation_names_do_not_erase_resource_fields():
    from crew_health.platform.domain.replication import effective_leaf_schemas

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"title": {"type": "string", "title": "Annotation"}},
    }
    assert effective_leaf_schemas(schema, maximum=16) == {"/title": {"type": "string"}}


def test_operational_catalog_excludes_nested_personal_metadata():
    from crew_health.kernel.replication import ProjectionKey
    from crew_health.platform.application.replication import _catalog_schema
    from crew_health.platform.domain.replication import catalog_leaf_schemas

    key = ProjectionKey("rules", "operational", 1)
    allowed = catalog_leaf_schemas(key, _catalog_schema(key), maximum=1024)
    assert "/warning_id" in allowed
    assert "/resolved/by/id" not in allowed
    assert "/person_id" not in allowed
    assert "/dismissed/reason" not in allowed


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://untrusted.invalid/schema"},
        {"$ref": "#/$defs/missing", "$defs": {}},
        {"$ref": "#/$defs/cycle", "$defs": {"cycle": {"$ref": "#/$defs/cycle"}}},
    ],
)
def test_schema_resolution_rejects_external_missing_and_cyclic_references(schema):
    from crew_health.platform.domain.replication import schema_constraints

    with pytest.raises(ProblemError):
        schema_constraints(schema, maximum=16)


def test_schema_resolution_rejects_projection_field_overflow():
    from crew_health.platform.domain.replication import schema_constraints

    schema = {"type": "object", "properties": {"one": {"type": "string"}, "two": {"type": "string"}}}
    with pytest.raises(ProblemError):
        schema_constraints(schema, maximum=1)


def test_nullable_objects_grant_leaves_while_open_maps_and_arrays_stay_unavailable():
    from crew_health.platform.domain.replication import effective_leaf_schemas

    schema = {
        "type": "object",
        "properties": {
            "nested": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {"label": {"type": "string"}},
                        "additionalProperties": False,
                    },
                    {"type": "null"},
                ]
            },
            "array": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]},
            "map": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "additionalProperties": False,
    }
    assert effective_leaf_schemas(schema, maximum=64) == {"/nested/label": {"type": "string"}}
