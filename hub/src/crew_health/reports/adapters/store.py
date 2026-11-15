"""ReportStore over the E01 private coordinator (SYSTEM 5.8.1, D18): every report version is one sealed private
entry; the hub keeps only the opaque index. This adapter never writes its own transaction, retry, idempotency or
crash recovery: it builds the PrivateIntent, calls the coordinator and maps what comes back.

Private reads need the store and vault together (PLAN P1: `PrivateStore` has no read method), so they come
through the `PrivateReader` port until E01 publishes one."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from crew_health.kernel.commands import CommandIdentity, canonical_json, command_identity
from crew_health.kernel.errors import ErrorCode, ProblemError, StorageUnavailable
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.private import (
    PrivateIntent,
    PrivateKeyVault,
    PrivatePublicationRejected,
    PrivateReceipt,
    PrivateRecordCoordinator,
    PrivateStore,
    SealedPrivateRef,
)
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext, UnitOfWork
from crew_health.reports.adapters.codec import decode_version, encode_version
from crew_health.reports.adapters.index import RECORD_KIND, ReportIndex, VersionRow
from crew_health.reports.domain.errors import ReportError, invalid_transition, not_found
from crew_health.reports.domain.model import Report


class PrivateReader(Protocol):
    """Decrypts one sealed entry for its owner (PLAN P1). The store and vault own the associated data."""

    def read(self, session: SessionContext, reference: SealedPrivateRef) -> bytes: ...


@dataclass(frozen=True, slots=True)
class PrivateServices:
    coordinator: PrivateRecordCoordinator
    store: PrivateStore
    vault: PrivateKeyVault
    reader: PrivateReader


@dataclass(frozen=True, slots=True)
class StoreLimits:
    max_entry_attempts: int  # reports.max_entry_attempts
    max_versions: int  # private.max_versions
    max_entry_bytes: int  # private.max_entry_bytes

    def __post_init__(self) -> None:
        if min(self.max_entry_attempts, self.max_versions, self.max_entry_bytes) < 1:
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)


def private_identity_bytes(intent: PrivateIntent) -> bytes:
    """The bytes the coordinator fingerprints (platform.application.private.private_identity_bytes). A module
    cannot import platform, so this copy is pinned to the original by a test."""
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


class PrivateReportStore:
    def __init__(
        self,
        *,
        services: PrivateServices,
        index: ReportIndex,
        snapshots: ReadSnapshotFactory,
        ids: IdFactory,
        limits: StoreLimits,
    ) -> None:
        self.s, self.index, self.snapshots, self.ids, self.limits = services, index, snapshots, ids, limits

    # reads ---------------------------------------------------------------------------------------------

    def _readable(self, session: SessionContext, report_id: str) -> VersionRow | None:
        with self.snapshots.snapshot() as view:
            row = self.index.current(view, report_id)
        if row is None or row.deleted or row.owner_person_id != session.person_id:
            return None
        return row

    def load(self, session: SessionContext, report_id: str) -> Report | None:
        row = self._readable(session, report_id)
        if row is None:
            return None
        decoded = decode_version(self.s.reader.read(session, row.reference()))
        if decoded.header.report_id != row.report_id or decoded.header.revision != row.revision:
            raise StorageUnavailable("corrupt")
        return decoded.report

    def read_source(self, session: SessionContext, report_id: str) -> bytes | None:
        with self.snapshots.snapshot() as view:
            row = self.index.source(view, report_id)
        if row is None or row.deleted or row.owner_person_id != session.person_id:
            return None
        decoded = decode_version(self.s.reader.read(session, row.reference()))
        if decoded.source is None or decoded.header.report_id != report_id:
            raise StorageUnavailable("corrupt")
        return decoded.source

    # writes --------------------------------------------------------------------------------------------

    def store_new(
        self,
        session: SessionContext,
        *,
        key: str,
        report: Report,
        source: bytes,
        apply: Callable[[UnitOfWork], bool],
    ) -> PrivateReceipt:
        owner = report.patient_id
        replay = self._committed(owner, key)
        if replay is not None:
            return PrivateReceipt(replay.entry_id, key, replay.revision)
        entry = self._fresh_entry(owner, key)
        plaintext = encode_version(report, source=source, source_entry_id=entry)
        intent = self._intent(
            session,
            owner=owner,
            key=key,
            operation="uploadReport",
            target="/v1/reports",
            parameters={"scope": report.access_scope, "origin_mode": report.origin_mode},
            entry_id=entry,
            version_id=self.ids.derived("rvr", report.report_id, "1"),
            previous=None,
            plaintext=plaintext,
        )
        return self._publish(session, intent, apply)

    def publish_version(
        self,
        session: SessionContext,
        *,
        key: str,
        operation: str,
        report: Report,
        apply: Callable[[UnitOfWork], bool],
    ) -> PrivateReceipt:
        owner = report.patient_id
        replay = self._committed(owner, key)
        if replay is not None:
            return PrivateReceipt(replay.entry_id, key, replay.revision)
        with self.snapshots.snapshot() as view:
            current = self.index.current(view, report.report_id)
            source = self.index.source(view, report.report_id)
        if current is None or source is None or current.deleted or current.owner_person_id != owner:
            raise not_found()
        entry = self._fresh_entry(owner, key)
        plaintext = encode_version(report, source=None, source_entry_id=source.entry_id)
        intent = self._intent(
            session,
            owner=owner,
            key=key,
            operation=operation,
            target=f"/v1/reports/{report.report_id}",
            parameters={"report_id": report.report_id, "revision": report.revision - 1},
            entry_id=entry,
            version_id=self.ids.derived("rvr", report.report_id, str(report.revision)),
            previous=current.version_id,
            plaintext=plaintext,
        )
        return self._publish(session, intent, apply)

    def delete(self, session: SessionContext, *, key: str, report_id: str) -> None:
        owner = session.person_id
        if owner is None:
            raise ReportError(ErrorCode.UNAUTHORIZED_SCOPE, "not_owner_session")
        with self.snapshots.snapshot() as view:
            entries = self.index.entries(view, report_id, limit=self.limits.max_versions)
            current = self.index.current(view, report_id)
        if not entries or current is None or current.owner_person_id != owner:
            raise not_found()
        identity = command_identity(
            key=key,
            actor_ref=session.actor.id,
            operation="deleteReport",
            target=f"/v1/reports/{report_id}",
            parameters_json=b"{}",
            body_fingerprint=hashlib.sha256(canonical_json(list(entries))).hexdigest(),
            fingerprint_kind="sha256",
        )
        self.s.coordinator.delete(session, identity=identity, owner_person_id=owner, entry_ids=entries)

    # helpers -------------------------------------------------------------------------------------------

    def _committed(self, owner: str, key: str) -> VersionRow | None:
        with self.snapshots.snapshot() as view:
            return self.index.by_command(view, owner, key)

    def _fresh_entry(self, owner: str, key: str) -> str:
        """The entry id for this command. A discarded orphan revokes its entry id for good, so a retry under the
        same key takes the next attempt number: bounded, deterministic, and replay-safe because a committed key
        is found by `_committed` first."""
        for attempt in range(self.limits.max_entry_attempts):
            entry = self.ids.derived("rep", owner, key, str(attempt))
            if not self.s.vault.is_revoked(entry_id=entry):
                return entry
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)

    def _intent(
        self,
        session: SessionContext,
        *,
        owner: str,
        key: str,
        operation: str,
        target: str,
        parameters: dict[str, object],
        entry_id: str,
        version_id: str,
        previous: str | None,
        plaintext: bytes,
    ) -> PrivateIntent:
        if len(plaintext) > self.limits.max_entry_bytes:
            raise ReportError(ErrorCode.PAYLOAD_TOO_LARGE, "too_large")
        provisional: CommandIdentity = command_identity(
            key=key,
            actor_ref=session.actor.id,
            operation=operation,
            target=target,
            parameters_json=canonical_json(parameters),
            body_fingerprint="",
            fingerprint_kind="hmac_sha256",
        )
        shell = PrivateIntent(provisional, owner, entry_id, RECORD_KIND, version_id, previous, plaintext)
        fingerprint = self.s.vault.fingerprint(
            owner_person_id=owner, canonical_identity_and_body=private_identity_bytes(shell)
        )
        return replace(shell, identity=replace(provisional, body_fingerprint=fingerprint))

    def _publish(
        self, session: SessionContext, intent: PrivateIntent, apply: Callable[[UnitOfWork], bool]
    ) -> PrivateReceipt:
        """Seal, then publish. A refusal raised inside `apply` rejects the publication (the coordinator then
        discards the orphan) and is re-raised here as itself; any other failure before the hub commit discards
        the orphan so a retry starts clean. A failure after the commit is left to the coordinator's recovery."""
        raised: list[Exception] = []
        returned_false: list[bool] = []

        def guarded(uow: UnitOfWork) -> bool:
            try:
                accepted = apply(uow)
            except Exception as error:
                raised.append(error)
                return False
            if not accepted:
                returned_false.append(True)
            return accepted

        reference = self.s.store.seal(intent)
        try:
            return self.s.coordinator.publish(session, intent, reference, guarded)
        except PrivatePublicationRejected:
            if raised:
                raise raised[0] from None
            raise (invalid_transition("late_generation") if returned_false else not_found()) from None
        except Exception:
            if self._committed(intent.owner_person_id, intent.identity.key) is None:
                self._discard(reference)
            raise

    def _discard(self, reference: SealedPrivateRef) -> None:
        try:
            if self.s.store.verify(reference):
                self.s.store.discard_orphan(reference)
        except ProblemError:
            return  # startup recovery repairs what this best effort could not
