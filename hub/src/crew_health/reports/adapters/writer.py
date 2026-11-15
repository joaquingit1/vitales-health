"""The reports PrivateReferenceWriter: attaches and tombstones the hub index rows in the coordinator's
transaction (SYSTEM 5.8.1). It reads only the opaque header of the plaintext, never private fields."""

from __future__ import annotations

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode, ProblemError, StorageUnavailable
from crew_health.kernel.private import PrivateIntent, SealedPrivateRef
from crew_health.kernel.uow import Audience, ReplicationChange, UnitOfWork
from crew_health.reports.adapters.codec import read_header
from crew_health.reports.application.workflows import (
    REPORT_AUDIENCE,
    REPORT_AUDIENCE_POLICY,
    REPORT_PROJECTION,
)


class ReportReferenceWriter:
    def attach(self, uow: UnitOfWork, reference: SealedPrivateRef, intent: PrivateIntent) -> None:
        header = read_header(intent.plaintext)
        if header.patient_id != reference.owner_person_id:
            raise ProblemError(ErrorCode.IDENTITY_MISMATCH, ErrorCode.IDENTITY_MISMATCH.value)
        rows = uow.connection.rows(
            "SELECT revision,deleted FROM reports_report WHERE report_id=?", (header.report_id,), limit=1
        )
        if not rows:
            if header.revision != 1 or header.source_entry_id != reference.entry_id:
                raise StorageUnavailable("corrupt")
            self._announce(uow, header.report_id)
            uow.connection.execute(
                "INSERT INTO reports_report(report_id,owner_person_id,current_entry_id,source_entry_id,revision) "
                "VALUES (?,?,?,?,1)",
                (header.report_id, reference.owner_person_id, reference.entry_id, reference.entry_id),
            )
        else:
            # the confirmation lineage advanced the revision in this same transaction
            if int(str(rows[0][0])) != header.revision or rows[0][1]:
                raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value)
            uow.connection.execute(
                "UPDATE reports_report SET current_entry_id=? WHERE report_id=?",
                (reference.entry_id, header.report_id),
            )
        uow.connection.execute(
            "INSERT INTO reports_version(entry_id,report_id,revision,version_id,owner_person_id,command_key,"
            "fingerprint,key_id,ciphertext_sha256) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                reference.entry_id,
                header.report_id,
                header.revision,
                reference.version_id,
                reference.owner_person_id,
                reference.command_key,
                reference.fingerprint,
                reference.key_id,
                reference.ciphertext_sha256,
            ),
        )

    @staticmethod
    def _announce(uow: UnitOfWork, report_id: str) -> None:
        """An opaque, owner-audience change naming only the report id. It registers the record so the audit
        trail can reference it (SYSTEM 5.7); no private field is in it and it is never exported to another person."""
        uow.replicate(
            ReplicationChange(
                None,
                "report",
                report_id,
                "create",
                canonical_json({"report_id": report_id}),
                (),
                uow.release.release_id,
                Audience(REPORT_AUDIENCE, report_id, REPORT_PROJECTION, REPORT_AUDIENCE_POLICY),
            )
        )

    def tombstone(self, uow: UnitOfWork, *, entry_ids: tuple[str, ...], revocation_ref: str) -> None:
        for entry_id in entry_ids:
            uow.connection.execute("UPDATE reports_version SET deleted=1 WHERE entry_id=?", (entry_id,))
            uow.connection.execute(
                "UPDATE reports_report SET deleted=1 WHERE report_id="
                "(SELECT report_id FROM reports_version WHERE entry_id=?)",
                (entry_id,),
            )
