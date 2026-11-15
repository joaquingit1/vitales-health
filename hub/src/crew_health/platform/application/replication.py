from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import replace

from jsonschema import Draft202012Validator

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.replication import (
    Delivery,
    ProjectionBody,
    ProjectionKey,
    ProjectionRegistration,
    Recipient,
    RevocationControl,
)
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import ReadSnapshot, ReplicationChange, UnitOfWork
from crew_health.platform.application.uow import codec
from crew_health.platform.domain.errors import fail
from crew_health.platform.domain.replication import (
    catalog_key,
    control_bytes,
    delivery_hash,
    parse_control,
    schema_leaf_fields,
    validate_body,
)


class ControlCodec:
    def dump_json(self, value: RevocationControl) -> bytes:
        return control_bytes(value)

    def validate_json(self, raw: bytes) -> RevocationControl:
        return parse_control(raw)


class ReplicaService:
    def __init__(
        self,
        *,
        registrations: tuple[ProjectionRegistration, ...],
        allowed_fields: Mapping[ProjectionKey, frozenset[str]],
        settings: HubSettings,
        ids: IdFactory,
        node_id: str,
        incarnation: str,
        authorize: Callable[[ReadSnapshot | UnitOfWork, Recipient, ProjectionBody | None], bool],
        secret: Callable[[Recipient], bytes],
        sign: Callable[[bytes], bytes],
        verify: Callable[[RevocationControl], bool],
        erase: Callable[[UnitOfWork, str], None],
        dependency: Callable[[UnitOfWork, str], bool],
    ) -> None:
        self.settings, self.ids, self.node_id, self.incarnation = settings, ids, node_id, incarnation
        self.authorize, self.secret, self.sign, self.verify_control, self.erase, self.dependency = (
            authorize,
            secret,
            sign,
            verify,
            erase,
            dependency,
        )
        self.registrations = {r.key: r for r in registrations}
        self.allowed_fields = dict(allowed_fields)
        self.validators: dict[ProjectionKey, Draft202012Validator] = {}
        self.deliveries, self.changes = codec(Delivery), codec(ReplicationChange)
        self.controls = ControlCodec()
        if len(self.registrations) != len(registrations):
            raise fail(ErrorCode.CONFIG_INVALID)
        for registration in registrations:
            if (
                not catalog_key(registration.key)
                or hashlib.sha256(registration.schema_json).hexdigest() != registration.schema_sha256
            ):
                raise fail(ErrorCode.CONFIG_INVALID)
            schema: object = json.loads(registration.schema_json)
            if not isinstance(schema, dict):
                raise fail(ErrorCode.CONFIG_INVALID)
            fields = schema_leaf_fields(schema, maximum=settings.integer("sync.max_projection_fields"))
            if not fields <= self.allowed_fields.get(registration.key, frozenset()):
                raise fail(ErrorCode.CONFIG_INVALID)
            Draft202012Validator.check_schema(schema)
            self.validators[registration.key] = Draft202012Validator(schema)

    def _stream(self, recipient: Recipient) -> str:
        return self.ids.derived(
            "stream", recipient.recipient_id, recipient.purpose, str(recipient.policy_epoch)
        )

    def _admit(
        self, view: ReadSnapshot | UnitOfWork, recipient: Recipient, body: ProjectionBody | None = None
    ) -> None:
        if recipient.policy_epoch < 1 or not self.authorize(view, recipient, body):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)

    def _body(self, body: ProjectionBody) -> None:
        if (
            body.key not in self.registrations
            or len(body.data_json) > self.settings.integer("sync.max_bytes")
            or len(body.dependencies) > self.settings.integer("sync.max_dependencies")
        ):
            raise fail(ErrorCode.CONFIG_INVALID)
        validate_body(
            body,
            allowed_fields=self.allowed_fields[body.key],
            maximum=self.settings.integer("sync.max_projection_fields"),
        )
        if not self.validators[body.key].is_valid(json.loads(body.data_json)):
            raise fail(ErrorCode.INVALID_VALUE)

    def _live(
        self, view: ReadSnapshot | UnitOfWork, recipient: Recipient, stream_id: str
    ) -> tuple[object, ...]:
        rows = view.connection.rows(
            "SELECT recipient_id,purpose,policy_epoch,scan_seq,delivery_seq,ack_seq,fenced FROM platform_replica_stream WHERE stream_id=?",
            (stream_id,),
            limit=1,
        )
        if (
            not rows
            or rows[0][:3] != (recipient.recipient_id, recipient.purpose, recipient.policy_epoch)
            or rows[0][6]
        ):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        return rows[0]

    def materialize(self, uow: UnitOfWork, recipient: Recipient, *, limit: int) -> int:
        self._admit(uow, recipient)
        if not 1 <= limit <= self.settings.integer("sync.page_entries"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        stream = self._stream(recipient)
        uow.connection.execute(
            "INSERT OR IGNORE INTO platform_replica_stream VALUES (?,?,?,?,0,0,0,0)",
            (stream, recipient.recipient_id, recipient.purpose, recipient.policy_epoch),
        )
        head = self._live(uow, recipient, stream)
        pending = int(str(head[4])) - int(str(head[5]))
        rows = uow.connection.rows(
            "SELECT seq,origin_node_id,origin_incarnation,change FROM repl_log WHERE seq>? ORDER BY seq LIMIT ?",
            (int(str(head[3])), limit),
            limit=limit,
        )
        sequence = int(str(head[4]))
        scanned = int(str(head[3]))
        for row in rows:
            scanned = int(str(row[0]))
            raw = row[3]
            if not isinstance(raw, bytes):
                raise fail(ErrorCode.INTERNAL_ERROR)
            change = self.changes.validate_json(raw)
            registration = (
                self.registrations.get(change.export_projection)
                if change.export_projection is not None
                else None
            )
            if registration is None:
                continue
            body = registration.provider.export(uow, change, recipient)
            if body is None:
                continue
            self._admit(uow, recipient, body)
            self._body(body)
            if pending >= self.settings.integer("sync.pending_max"):
                raise fail(ErrorCode.INTERNAL_ERROR)
            token = hmac.new(
                self.secret(recipient), canonical_json([row[1], row[2], row[0]]), hashlib.sha256
            ).hexdigest()
            sequence += 1
            delivery = Delivery(
                stream,
                recipient.policy_epoch,
                sequence,
                recipient.recipient_id,
                recipient.purpose,
                str(row[1]),
                str(row[2]),
                token,
                body,
                "",
            )
            delivery = replace(delivery, envelope_sha256=delivery_hash(delivery))
            uow.connection.execute(
                "INSERT INTO platform_replica_delivery VALUES (?,?,?,?)",
                (stream, sequence, self.deliveries.dump_json(delivery), delivery.envelope_sha256),
            )
            pending += 1
        uow.connection.execute(
            "UPDATE platform_replica_stream SET scan_seq=?,delivery_seq=? WHERE stream_id=?",
            (scanned, sequence, stream),
        )
        return sequence - int(str(head[4]))

    def page(
        self, snapshot: ReadSnapshot, recipient: Recipient, *, stream_id: str, after: int, limit: int
    ) -> tuple[Delivery, ...]:
        self._admit(snapshot, recipient)
        self._live(snapshot, recipient, stream_id)
        if after < 0 or not 1 <= limit <= self.settings.integer("sync.page_entries"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        rows = snapshot.connection.rows(
            "SELECT envelope FROM platform_replica_delivery WHERE stream_id=? AND seq>? ORDER BY seq LIMIT ?",
            (stream_id, after, limit),
            limit=limit,
        )
        result: list[Delivery] = []
        for row in rows:
            if not isinstance(row[0], bytes):
                raise fail(ErrorCode.INTERNAL_ERROR)
            delivery = self.deliveries.validate_json(row[0])
            # A narrowed grant fences the whole stream, never deletes a gap.
            self._admit(snapshot, recipient, delivery.body)
            result.append(delivery)
        return tuple(result)

    def apply(self, uow: UnitOfWork, recipient: Recipient, delivery: Delivery) -> int:
        self._admit(uow, recipient, delivery.body)
        if (delivery.recipient_id, delivery.purpose, delivery.policy_epoch) != (
            recipient.recipient_id,
            recipient.purpose,
            recipient.policy_epoch,
        ) or delivery.envelope_sha256 != delivery_hash(delivery):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        self._body(delivery.body)
        uow.connection.execute(
            "INSERT OR IGNORE INTO platform_replica_stream VALUES (?,?,?,?,0,0,0,0)",
            (delivery.stream_id, recipient.recipient_id, recipient.purpose, recipient.policy_epoch),
        )
        self._live(uow, recipient, delivery.stream_id)
        rows = uow.connection.rows(
            "SELECT seq,envelope_sha256 FROM platform_replica_applied WHERE stream_id=? ORDER BY seq DESC LIMIT 1",
            (delivery.stream_id,),
            limit=1,
        )
        previous = int(str(rows[0][0])) if rows else 0
        if delivery.delivery_seq <= previous:
            old = uow.connection.rows(
                "SELECT envelope_sha256 FROM platform_replica_applied WHERE stream_id=? AND seq=?",
                (delivery.stream_id, delivery.delivery_seq),
                limit=1,
            )
            if not old or old[0][0] != delivery.envelope_sha256:
                raise fail(ErrorCode.INVALID_VALUE)
            return previous
        if delivery.delivery_seq != previous + 1:
            raise fail(ErrorCode.INVALID_TRANSITION)
        for dependency in delivery.body.dependencies:
            if dependency.disposition == "required" and (
                dependency.record_ref is None or not self.dependency(uow, dependency.record_ref)
            ):
                raise fail(ErrorCode.INVALID_TRANSITION)
        self.registrations[delivery.body.key].applier.apply(uow, delivery, recipient)
        uow.connection.execute(
            "INSERT INTO platform_replica_applied VALUES (?,?,?)",
            (delivery.stream_id, delivery.delivery_seq, delivery.envelope_sha256),
        )
        return delivery.delivery_seq

    def acknowledge(
        self, uow: UnitOfWork, recipient: Recipient, *, stream_id: str, through: int, envelope_sha256: str
    ) -> None:
        self._admit(uow, recipient)
        head = self._live(uow, recipient, stream_id)
        rows = uow.connection.rows(
            "SELECT envelope_sha256 FROM platform_replica_delivery WHERE stream_id=? AND seq=?",
            (stream_id, through),
            limit=1,
        )
        if not rows or rows[0][0] != envelope_sha256 or through < int(str(head[5])):
            raise fail(ErrorCode.INVALID_VALUE)
        uow.connection.execute(
            "UPDATE platform_replica_stream SET ack_seq=? WHERE stream_id=?", (through, stream_id)
        )

    def revoke(self, uow: UnitOfWork, recipient: Recipient, *, stream_id: str) -> RevocationControl:
        self._admit(uow, recipient)
        controls = uow.connection.rows(
            "SELECT control FROM platform_revocation WHERE recipient_id=? AND stream_id=? AND old_epoch=?",
            (recipient.recipient_id, stream_id, recipient.policy_epoch),
            limit=1,
        )
        if controls:
            if not isinstance(controls[0][0], bytes):
                raise fail(ErrorCode.INTERNAL_ERROR)
            existing = parse_control(controls[0][0])
            if (existing.stream_id, existing.recipient_id, existing.old_policy_epoch) != (
                stream_id,
                recipient.recipient_id,
                recipient.policy_epoch,
            ):
                raise fail(ErrorCode.INVALID_VALUE)
            return existing
        head = self._live(uow, recipient, stream_id)
        control = RevocationControl(
            self.ids.new("control"),
            recipient.recipient_id,
            stream_id,
            int(str(head[2])),
            recipient.policy_epoch + 1,
            self.node_id,
            b"",
        )
        payload = control_bytes(control)
        control = replace(control, signature=self.sign(payload))
        uow.connection.execute("UPDATE platform_replica_stream SET fenced=1 WHERE stream_id=?", (stream_id,))
        uow.connection.execute(
            "INSERT INTO platform_revocation VALUES (?,?,0,?,?,?)",
            (
                control.control_id,
                control_bytes(control),
                control.recipient_id,
                control.stream_id,
                control.old_policy_epoch,
            ),
        )
        return control

    def apply_revocation(self, uow: UnitOfWork, control: RevocationControl) -> str:
        if not self.verify_control(control) or control.new_policy_epoch <= control.old_policy_epoch:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        rows = uow.connection.rows(
            "SELECT control FROM platform_revocation WHERE control_id=?", (control.control_id,), limit=1
        )
        raw = control_bytes(control)
        if rows:
            if rows[0][0] != raw:
                raise fail(ErrorCode.INVALID_VALUE)
            return control.control_id
        stream = uow.connection.rows(
            "SELECT recipient_id,policy_epoch FROM platform_replica_stream WHERE stream_id=?",
            (control.stream_id,),
            limit=1,
        )
        if stream and (
            stream[0][0] != control.recipient_id or int(str(stream[0][1])) > control.old_policy_epoch
        ):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        uow.connection.execute(
            "INSERT INTO platform_replica_stream VALUES (?,?,?,?,0,0,0,1) ON CONFLICT(stream_id) DO UPDATE SET fenced=1, policy_epoch=excluded.policy_epoch",
            (control.stream_id, control.recipient_id, "", control.new_policy_epoch),
        )
        self.erase(uow, control.stream_id)
        uow.connection.execute(
            "INSERT INTO platform_revocation VALUES (?,?,0,?,?,?)",
            (control.control_id, raw, control.recipient_id, control.stream_id, control.old_policy_epoch),
        )
        return control.control_id

    def acknowledge_revocation(self, uow: UnitOfWork, *, control_id: str, recipient_id: str) -> None:
        rows = uow.connection.rows(
            "SELECT control FROM platform_revocation WHERE control_id=?", (control_id,), limit=1
        )
        if not rows or not isinstance(rows[0][0], bytes):
            raise fail(ErrorCode.NOT_FOUND)
        control = parse_control(rows[0][0])
        if control.recipient_id != recipient_id:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        uow.connection.execute(
            "UPDATE platform_revocation SET acknowledged=1 WHERE control_id=?", (control_id,)
        )
