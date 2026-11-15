"""Report use cases against injected ports (E09; SYSTEM 5.7, 5.8, 5.8.1, 5.10.1).

No transaction, idempotency, retry or encryption mechanism is written here: private writes go through the
store port (PrivateRecordCoordinator), hub work through the caller's UnitOfWork. Findings reach clinical
records only through the FindingRecorder port in the confirming unit of work, never directly. The assistant
and every non-owner session are refused before a report is loaded. Replay of a command key never duplicates
a finding; reuse of a key with another body is the boundary's CommandIdentity conflict (T3)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from decimal import Decimal

from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandIdentity, CommandReceipts, IdempotencyStore, StoredResult
from crew_health.kernel.config import ConfigReleaseReader, ReleaseHandle
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.tasks import TaskLease, TaskResult, TaskRunner
from crew_health.kernel.units import UnitRegistry
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, SessionContext, UnitOfWork, UnitOfWorkFactory
from crew_health.reports.application.access import require_owner_session, require_report_owner
from crew_health.reports.application.ports import (
    ConfirmationLineage,
    FindingPolicies,
    FindingRecorder,
    FormDefinitions,
    ReportStore,
)
from crew_health.reports.domain.confirmation import ConfirmationCommand, decide
from crew_health.reports.domain.errors import (
    ReportError,
    invalid_transition,
    invalid_value,
    not_found,
    stale_version,
)
from crew_health.reports.domain.extraction import (
    ExtractionResult,
    ReportLimits,
    parse_pin,
    pin_ref,
    publish_extraction,
)
from crew_health.reports.domain.findings import Provenance, SpecialistEntry, validate_entry
from crew_health.reports.domain.media import check_media, sha256_hex
from crew_health.reports.domain.model import (
    ConfirmedFinding,
    Report,
    access_scope_of,
    origin_mode_of,
    parse_source_date,
)

_MAX_NAME_CHARS = 255  # file-name length bound shared by the supported platforms; no domain meaning


@dataclass(frozen=True, slots=True)
class ReportPorts:
    store: ReportStore
    lineage: ConfirmationLineage
    recorder: FindingRecorder
    policies: FindingPolicies
    forms: FormDefinitions
    units: UnitRegistry
    tasks: TaskRunner
    uow: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    idempotency: IdempotencyStore
    receipts: CommandReceipts
    releases: ConfigReleaseReader
    ids: IdFactory
    clock: Clock
    settings: HubSettings
    limits: ReportLimits


def _check_name(name: str) -> None:
    if not name or len(name) > _MAX_NAME_CHARS or any(ord(char) < 32 for char in name):
        raise invalid_value("original_name", field="file")


class _Replay(Exception):  # noqa: N818  # control signal inside one apply, never crosses the module
    """The command key already committed; roll back this unit of work and replay the stored result."""


class ReportWorkflows:
    def __init__(self, ports: ReportPorts) -> None:
        self.p = ports

    def upload(
        self,
        session: SessionContext,
        *,
        key: str,
        data: bytes,
        original_name: str,
        declared_type: str | None,
        access_scope: str | None,
        examination_date: str | None,
        report_date: str | None,
        origin_mode: str,
    ) -> Report:
        """E09-01. Policy first, then one private write. Missing access_scope is the narrowest scope."""
        person = require_owner_session(session)
        require_id(key)
        _check_name(original_name)
        scope = access_scope_of(access_scope)
        mode = origin_mode_of(origin_mode)
        decision = check_media(
            data, declared_type=declared_type, max_bytes=self.p.settings.integer("private.max_report_bytes")
        )
        digest = sha256_hex(data)
        report_id = self.p.ids.derived("rpt", person, key)
        existing = self.p.store.load(session, report_id)
        if existing is not None:
            if existing.file_sha256 != digest:
                raise ReportError(ErrorCode.DUPLICATE_COMMAND, "key_reused")
            return existing
        report = Report(
            report_id,
            digest,
            original_name,
            decision.media_type,
            len(data),
            person,
            None,
            parse_source_date(examination_date),
            parse_source_date(report_date),
            scope,
            self.p.clock.now(),
            (),
            mode,
            self.p.ids.derived("ver", report_id, digest),
            1,
            decision.state,
            decision.reason,
            (),
            None,
        )
        self.p.store.store_new(session, key=key, report=report, source=data)
        return report

    def get_report(self, session: SessionContext, report_id: str) -> Report:
        require_owner_session(session)
        report = self.p.store.load(session, require_id(report_id))
        if report is None or report.state == "deleted":
            raise not_found()
        require_report_owner(session, report.patient_id)
        return report

    def record_extraction(
        self, session: SessionContext, lease: TaskLease, result: ExtractionResult
    ) -> Report:
        """D17/D18: a draft is published only by the current generation, pinned to the source version."""
        if lease.task.public_kind != "report_extraction" or lease.task.actor.kind != "person":
            raise invalid_transition("not_extraction_task")
        report_id, version = parse_pin(lease.task.input_ref)
        report = self.p.store.load(session, report_id)
        if report is None or report.state == "deleted":
            raise not_found()
        if lease.task.actor != Actor("person", report.patient_id):
            raise ReportError(ErrorCode.UNAUTHORIZED_SCOPE, "task_actor")
        if version != report.source_version_id:
            raise stale_version()
        if lease.task.handler_version != result.engine_version:
            raise invalid_transition("engine_pin")
        after = publish_extraction(
            report,
            result,
            generation=lease.generation,
            release_id=lease.task.release_id,
            ids=self.p.ids,
            limits=self.p.limits,
        )
        fingerprint = hashlib.sha256(
            "\n".join(d.draft_id for d in after.extraction_drafts).encode()
        ).hexdigest()
        outcome = TaskResult(
            lease.task.task_id, lease.generation, pin_ref(report_id, after.source_version_id), fingerprint
        )

        def apply(uow: UnitOfWork) -> bool:
            self._advance(uow, report, after)
            return self.p.tasks.complete(uow, lease, outcome)

        key = self.p.ids.derived("cmd", lease.task.task_id, str(lease.generation))
        self.p.store.publish_version(session, key=key, report=after, apply=apply)
        return after

    def confirm(
        self,
        session: SessionContext,
        *,
        key: str,
        report_id: str,
        command: ConfirmationCommand,
    ) -> Report:
        """E09-02. One revision, one accepted result; findings append in the same unit of work."""
        person = require_owner_session(session)
        report = self.p.store.load(session, require_id(report_id))
        if report is None or report.state == "deleted":
            raise not_found()
        require_report_owner(session, report.patient_id)
        command = replace(command, confirmer_id=person, confirmed_at=self.p.clock.now())
        with self.p.snapshots.snapshot() as view:
            if self.p.lineage.applied(view, command_key=key):
                return report
        release = self._pinned_release(report)
        outcome = decide(
            report,
            command,
            policies=lambda code: self.p.policies.for_code(release, code),
            units=self.p.units,
            ids=self.p.ids,
            limits=self.p.limits,
        )

        def apply(uow: UnitOfWork) -> bool:
            if self.p.lineage.applied(uow, command_key=key):
                raise _Replay
            self._advance(uow, report, outcome.report)
            recorded = tuple(self._record(uow, session, f) for f in outcome.findings)
            self.p.lineage.append(uow, recorded, command_key=key)
            rejected = sum(d.review_status == "rejected" for d in outcome.report.extraction_drafts)
            uow.audit(
                AuditEntry(
                    self.p.ids.new("audit"),
                    "reports.confirmed",
                    1,
                    uow.actor,
                    {"report_id": report.report_id, "revision": str(outcome.report.revision)},
                    uow.correlation_id,
                    None,
                    {"findings": len(recorded), "rejected_total": rejected},
                )
            )
            return True

        try:
            self.p.store.publish_version(session, key=key, report=outcome.report, apply=apply)
        except _Replay:
            pass  # another submission of this key committed first: replay its stored result
        stored = self.p.store.load(session, report.report_id)
        if stored is None:
            raise not_found()
        return stored

    def _pinned_release(self, report: Report) -> ReleaseHandle:
        """Policies come from the release the extraction ran under, never the active one (fail closed)."""
        if report.extraction_release_id is None:
            raise invalid_transition("release_not_pinned")
        try:
            return self.p.releases.release(report.extraction_release_id)
        except ProblemError as error:
            raise ReportError(ErrorCode.CONFIG_INVALID, "pinned_release_unavailable") from error

    def enter_finding(
        self, session: SessionContext, identity: CommandIdentity, entry: SpecialistEntry
    ) -> tuple[ConfirmedFinding, ...]:
        """E09-03 backup path: a person types the finding; no report, parser or model is involved."""
        person = require_owner_session(session)
        if entry.patient_id != person:
            raise not_found()
        with self.p.uow.begin(actor=session.actor, command=identity, correlation_id=identity.key) as uow:
            prior = self.p.idempotency.lookup(uow, identity)
            if prior is not None:
                return self.p.lineage.findings_of(uow, command_key=identity.key)
            form = self.p.forms.get(uow.release, entry.form_id)
            if form is None:
                raise invalid_value("form_unknown")
            who = Provenance(person, uow.now, self.p.ids, identity.key)
            valid = validate_entry(form, entry, units=self.p.units, who=who)
            recorded = tuple(self._record(uow, session, f) for f in valid)
            self.p.lineage.append(uow, recorded, command_key=identity.key)
            receipt = self.p.receipts.accepted(uow, identity)
            self.p.idempotency.record(uow, identity, StoredResult(receipt, 201, None, "", None))
            return recorded

    def _advance(self, uow: UnitOfWork, before: Report, after: Report) -> None:
        if not self.p.lineage.advance(
            uow, report_id=before.report_id, expected_revision=before.revision, new_revision=after.revision
        ):
            raise stale_version()

    def _record(
        self, uow: UnitOfWork, session: SessionContext, finding: ConfirmedFinding
    ) -> ConfirmedFinding:
        if finding.role == "interpretation" or not isinstance(finding.value, Decimal):
            return finding
        return replace(finding, record_ref=self.p.recorder.record(uow, session, finding))


__all__ = ["ReportPorts", "ReportWorkflows"]
