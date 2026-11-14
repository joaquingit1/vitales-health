from __future__ import annotations

import hmac
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import (
    CommandIdentity,
    CommandReceipts,
    IdempotencyStore,
    StoredResult,
    canonical_json,
)
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.private import (
    PrivateDeletionResult,
    PrivateIntent,
    PrivateKeyVault,
    PrivatePublicationRejected,
    PrivateReceipt,
    PrivateReferenceWriter,
    PrivateStore,
    SealedPrivateRef,
)
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    ReadSnapshot,
    ReadSnapshotFactory,
    ReplicationChange,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.platform.domain.commands import identity_bytes
from crew_health.platform.domain.errors import fail


class PrivateLedger(IdempotencyStore, CommandReceipts, Protocol):
    pass


class PrivateReferenceStore(Protocol):
    def get(
        self, view: ReadSnapshot | UnitOfWork, entry_id: str
    ) -> tuple[SealedPrivateRef, PrivateReceipt, bool] | None: ...
    def attach(self, uow: UnitOfWork, reference: SealedPrivateRef) -> PrivateReceipt: ...


def private_identity_bytes(intent: PrivateIntent) -> bytes:
    metadata = canonical_json(
        {
            "operation": intent.identity.operation,
            "target": intent.identity.target,
            "parameters_sha256": intent.identity.parameters_sha256,
            "owner": intent.owner_person_id,
            "entry": intent.entry_id,
            "version": intent.version_id,
            "record_kind": intent.record_kind,
            "source_version": intent.source_version_id,
        }
    )
    return metadata + b"\0" + len(intent.plaintext).to_bytes(8, "big") + intent.plaintext


class PrivateCoordinator:
    def __init__(
        self,
        *,
        transactions: UnitOfWorkFactory,
        snapshots: ReadSnapshotFactory,
        store: PrivateStore,
        vault: PrivateKeyVault,
        writers: Mapping[str, PrivateReferenceWriter],
        ledger: PrivateLedger,
        references: PrivateReferenceStore,
        ids: IdFactory,
        settings: HubSettings,
        authorize: Callable[[SessionContext, str], bool],
    ) -> None:
        self.transactions, self.snapshots, self.store, self.vault = transactions, snapshots, store, vault
        self.writers, self.ledger, self.settings, self.authorize = dict(writers), ledger, settings, authorize
        self.references, self.ids = references, ids
        self._lock = threading.Lock()
        self._local = threading.local()

    @contextmanager
    def _barrier(self) -> Iterator[None]:
        if getattr(self._local, "entered", False):
            raise fail(ErrorCode.INVALID_TRANSITION)
        if not self._lock.acquire(timeout=self.settings.integer("private.coordinator_timeout_ms") / 1000):
            raise StorageUnavailable("locked")
        self._local.entered = True
        try:
            yield
        finally:
            self._local.entered = False
            self._lock.release()

    def _verify(self, session: SessionContext, intent: PrivateIntent) -> None:
        if (
            session.actor.id != intent.identity.actor_ref
            or not self.authorize(session, intent.owner_person_id)
            or intent.record_kind not in self.writers
        ):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        if len(intent.plaintext) > self.settings.integer("private.max_entry_bytes"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        fingerprint = self.vault.fingerprint(
            owner_person_id=intent.owner_person_id, canonical_identity_and_body=private_identity_bytes(intent)
        )
        if intent.identity.fingerprint_kind != "hmac_sha256" or not hmac.compare_digest(
            fingerprint, intent.identity.body_fingerprint
        ):
            raise fail(ErrorCode.IDENTITY_MISMATCH)
        if self.vault.is_revoked(entry_id=intent.entry_id):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        if intent.source_version_id is not None:
            with self.snapshots.snapshot() as view:
                rows = view.connection.rows(
                    "SELECT entry_id,owner_person_id,deleted FROM platform_private_reference WHERE version_id=?",
                    (intent.source_version_id,),
                    limit=1,
                )
                if (
                    not rows
                    or rows[0][1] != intent.owner_person_id
                    or rows[0][2]
                    or self.vault.is_revoked(entry_id=str(rows[0][0]))
                ):
                    raise PrivatePublicationRejected()

    def write(self, session: SessionContext, intent: PrivateIntent) -> PrivateReceipt:
        with self._barrier():
            self._verify(session, intent)
            reference = self.store.seal(intent)
            return self._publish(session, intent, reference, lambda uow: True)

    def publish(
        self,
        session: SessionContext,
        intent: PrivateIntent,
        reference: SealedPrivateRef,
        apply: Callable[[UnitOfWork], bool],
    ) -> PrivateReceipt:
        with self._barrier():
            try:
                self._verify(session, intent)
                return self._publish(session, intent, reference, apply)
            except PrivatePublicationRejected:
                with self.snapshots.snapshot() as view:
                    committed = self.references.get(view, reference.entry_id)
                if (
                    committed is None
                    and reference.owner_person_id == intent.owner_person_id
                    and reference.entry_id == intent.entry_id
                    and self.store.verify(reference)
                ):
                    self.store.discard_orphan(reference)
                raise

    def _publish(
        self,
        session: SessionContext,
        intent: PrivateIntent,
        reference: SealedPrivateRef,
        apply: Callable[[UnitOfWork], bool],
    ) -> PrivateReceipt:
        if (
            reference.owner_person_id,
            reference.entry_id,
            reference.version_id,
            reference.command_key,
            reference.fingerprint,
            reference.record_kind,
        ) != (
            intent.owner_person_id,
            intent.entry_id,
            intent.version_id,
            intent.identity.key,
            intent.identity.body_fingerprint,
            intent.record_kind,
        ) or not self.store.verify(reference):
            raise StorageUnavailable("corrupt")
        with self.transactions.begin(
            actor=session.actor, command=intent.identity, correlation_id=intent.identity.key
        ) as uow:
            prior = self.ledger.lookup(uow, intent.identity)
            existing = self.references.get(uow, intent.entry_id)
            if prior is not None:
                if existing is None or existing[0] != reference or existing[2]:
                    raise StorageUnavailable("corrupt")
                receipt = existing[1]
            else:
                if not apply(uow):
                    raise PrivatePublicationRejected()
                self.writers[intent.record_kind].attach(uow, reference, intent)
                receipt = self.references.attach(uow, reference)
                accepted = self.ledger.accepted(uow, intent.identity)
                self.ledger.record(
                    uow, intent.identity, StoredResult(accepted, 200, intent.entry_id, "accepted", None)
                )
                uow.replicate(
                    ReplicationChange(
                        None,
                        "private_reference",
                        intent.entry_id,
                        "create",
                        canonical_json({"entry_id": intent.entry_id, "version_id": intent.version_id}),
                        (),
                        uow.release.release_id,
                        Audience(
                            "private.owner",
                            intent.entry_id,
                            AudienceProjectionKey("platform", "private", 1),
                            "1",
                        ),
                    )
                )
                uow.audit(
                    AuditEntry(
                        self.ids.new("audit"),
                        "private.accepted",
                        1,
                        session.actor,
                        {"entry": intent.entry_id},
                        uow.correlation_id,
                        None,
                        {},
                    )
                )
        self.store.acknowledge(reference, receipt)
        return receipt

    def recover(self, *, limit: int) -> int:
        with self._barrier():
            return self._recover(limit=limit)

    def _recover(self, *, limit: int) -> int:
        if not 1 <= limit <= self.settings.integer("private.recovery_batch"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        repaired = 0
        for reference in self.store.pending(after_entry_id=None, limit=limit):
            with self.snapshots.snapshot() as view:
                stored = self.references.get(view, reference.entry_id)
            if stored is None:
                self.store.discard_orphan(reference)
            elif not stored[2]:
                if stored[0] != reference or not self.store.verify(reference):
                    raise StorageUnavailable("corrupt")
                self.store.acknowledge(reference, stored[1])
            repaired += 1
        self._verify_live_references()
        return repaired

    def verify_live_references(self) -> int:
        with self._barrier():
            return self._verify_live_references()

    def _verify_live_references(self) -> int:
        checked, after = 0, ""
        maximum = self.settings.integer("private.max_versions")
        page_size = min(
            self.settings.integer("private.recovery_batch"), self.settings.integer("storage.max_rows")
        )
        with self.snapshots.snapshot() as view:
            for _ in range(maximum // page_size + 1):
                rows = view.connection.rows(
                    "SELECT entry_id FROM platform_private_reference WHERE deleted=0 AND entry_id>? ORDER BY entry_id LIMIT ?",
                    (after, page_size),
                    limit=page_size,
                )
                if not rows:
                    return checked
                for row in rows:
                    after = str(row[0])
                    stored = self.references.get(view, after)
                    if stored is None or not self.store.verify(stored[0]):
                        raise StorageUnavailable("corrupt")
                    checked += 1
                    if checked > maximum:
                        raise StorageUnavailable("corrupt")
                if len(rows) < page_size:
                    return checked
        raise StorageUnavailable("corrupt")

    @contextmanager
    def backup_barrier(self) -> Iterator[None]:
        with self._barrier():
            self._recover(limit=self.settings.integer("private.recovery_batch"))
            if self.store.pending(after_entry_id=None, limit=1):
                raise fail(ErrorCode.INTERNAL_ERROR)
            yield

    def delete(
        self,
        session: SessionContext,
        *,
        identity: CommandIdentity,
        owner_person_id: str,
        entry_ids: tuple[str, ...],
    ) -> PrivateDeletionResult:
        with self._barrier():
            if not self.authorize(session, owner_person_id) or identity.actor_ref != session.actor.id:
                raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
            if not 1 <= len(entry_ids) <= self.settings.integer("private.max_versions") or len(
                set(entry_ids)
            ) != len(entry_ids):
                raise fail(ErrorCode.INVALID_VALUE)
            with self.transactions.begin(
                actor=session.actor, command=identity, correlation_id=identity.key
            ) as uow:
                for entry_id in entry_ids:
                    reference = self.references.get(uow, entry_id)
                    if reference is None or reference[0].owner_person_id != owner_person_id:
                        raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
                prior = uow.connection.rows(
                    "SELECT identity, entry_ids FROM platform_private_deletion WHERE actor_ref=? AND command_key=?",
                    (identity.actor_ref, identity.key),
                    limit=1,
                )
                raw_ids = canonical_json(entry_ids)
                if prior and prior[0] != (identity_bytes(identity), raw_ids):
                    raise fail(ErrorCode.DUPLICATE_COMMAND)
                uow.connection.execute(
                    "INSERT OR IGNORE INTO platform_private_deletion(actor_ref,command_key,identity,owner_person_id,entry_ids) VALUES (?,?,?,?,?)",
                    (identity.actor_ref, identity.key, identity_bytes(identity), owner_person_id, raw_ids),
                )
            revocation = self.vault.revoke(
                owner_person_id=owner_person_id, entry_ids=entry_ids, command_key=identity.key
            )
            if not revocation.complete:
                return PrivateDeletionResult(entry_ids, identity.key, revocation, None)
            with self.transactions.begin(
                actor=session.actor, command=identity, correlation_id=identity.key
            ) as uow:
                rows = uow.connection.rows(
                    "SELECT completed FROM platform_private_deletion WHERE actor_ref=? AND command_key=?",
                    (identity.actor_ref, identity.key),
                    limit=1,
                )
                if not rows[0][0]:
                    for kind, writer in sorted(self.writers.items()):
                        owned: list[str] = []
                        for entry_id in sorted(entry_ids):
                            reference = self.references.get(uow, entry_id)
                            if reference is not None and reference[0].record_kind == kind:
                                owned.append(entry_id)
                        if owned:
                            writer.tombstone(
                                uow, entry_ids=tuple(owned), revocation_ref=revocation.revocation_id
                            )
                    for entry_id in entry_ids:
                        uow.connection.execute(
                            "UPDATE platform_private_reference SET deleted=1 WHERE entry_id=?", (entry_id,)
                        )
                        uow.replicate(
                            ReplicationChange(
                                None,
                                "private_reference",
                                entry_id,
                                "retract",
                                canonical_json({"entry_id": entry_id}),
                                (),
                                uow.release.release_id,
                                Audience(
                                    "private.owner",
                                    entry_id,
                                    AudienceProjectionKey("platform", "private", 1),
                                    "1",
                                ),
                            )
                        )
                    uow.connection.execute(
                        "UPDATE platform_private_deletion SET completed=1, revocation_id=? WHERE actor_ref=? AND command_key=?",
                        (revocation.revocation_id, identity.actor_ref, identity.key),
                    )
                    self.ledger.record(
                        uow,
                        identity,
                        StoredResult(self.ledger.accepted(uow, identity), 200, None, "deleted", None),
                    )
                    uow.replicate(
                        ReplicationChange(
                            None,
                            "private_revocation",
                            revocation.revocation_id,
                            "retract",
                            canonical_json({"revocation_id": revocation.revocation_id}),
                            (),
                            uow.release.release_id,
                            Audience(
                                "private.owner",
                                revocation.revocation_id,
                                AudienceProjectionKey("platform", "private", 1),
                                "1",
                            ),
                        )
                    )
                    uow.audit(
                        AuditEntry(
                            self.ids.new("audit"),
                            "private.deleted",
                            1,
                            session.actor,
                            {"revocation": revocation.revocation_id},
                            uow.correlation_id,
                            None,
                            {},
                        )
                    )
                ref = self.references.get(uow, entry_ids[-1])
                if ref is None:
                    raise StorageUnavailable("corrupt")
                return PrivateDeletionResult(entry_ids, identity.key, revocation, ref[1].hub_reference_seq)
