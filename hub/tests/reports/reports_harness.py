"""Explicit hermetic station for the report workflows: kernel fakes plus transactional in-memory ports.

Never a production fallback. The journal makes the ports of this module roll back with the kernel unit of
work, so crash and conflict tests prove atomicity without SQLite (T2 owns the real repositories)."""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import CommandIdentity, StoredResult, command_identity
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.private import PrivatePublicationRejected, PrivateReceipt
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.tasks import TaskInput, TaskLease, TaskRegistration, TaskResult
from crew_health.kernel.testing import (
    MemoryClock,
    MemoryCommands,
    MemoryConfig,
    MemoryIds,
    MemoryRelease,
    MemorySettings,
    MemoryStorage,
    MemoryTaskHandler,
    MemoryTasks,
    MemoryUnits,
    MemoryUowFactory,
)
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    SessionContext,
    UnitOfWork,
)
from crew_health.reports.application.workflows import ReportPorts, ReportWorkflows
from crew_health.reports.domain.extraction import ReportLimits
from crew_health.reports.domain.findings import (
    FieldDefinition,
    FindingPolicy,
    FormDefinition,
)
from crew_health.reports.domain.model import ConfirmedFinding, Report

AT = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
OWNER = "mara"
OTHER = "niko"
MAX_REPORT = 20000000
DICTIONARY = Path(__file__).resolve().parents[3] / "docs" / "product" / "input-dictionary.csv"

PDF = b"%PDF-1.7\n1 0 obj<< /Type /Catalog >>endobj\ntrailer<< >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 17 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9"

UNITS = MemoryUnits(
    {
        "mmol/L": ("amount_concentration", Decimal(1), Decimal(0)),
        "mg/dL": ("mass_concentration", Decimal(1), Decimal(0)),
        "mGy": ("absorbed_dose", Decimal(1), Decimal(0)),
        "uSv": ("dose_equivalent", Decimal(1), Decimal(0)),
        "mm[Hg]": ("pressure", Decimal(1), Decimal(0)),
    }
)


def session(
    person: str | None = OWNER,
    *,
    kind: str = "crew",
    actor_kind: str = "person",
    roles: frozenset[Role] = frozenset({Role.CREW}),
) -> SessionContext:
    return SessionContext("session", kind, Actor(actor_kind, person or "none"), person, roles)  # type: ignore[arg-type]


def numeric(code: str, unit: str) -> FindingPolicy:
    return FindingPolicy(code, "numeric", frozenset({unit}), "test golden")


def text(code: str) -> FindingPolicy:
    return FindingPolicy(code, "text", frozenset(), "test golden")


def dictionary_row(field_id: str) -> dict[str, str]:
    """A row of the product input dictionary: forms in tests follow the document, never typed content."""
    with DICTIONARY.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["field_id"] == field_id:
                return row
    raise KeyError(field_id)


def required_attributes(row: Mapping[str, str]) -> frozenset[str]:
    """The attribute names a dictionary validation sentence says are required."""
    sentence = row["validation"].split(";")[0]
    head = sentence.removeprefix("Preserve ").split(" required")[0]
    parts = head.replace(" and ", ", ").split(", ")
    return frozenset(p.strip().lower().replace(" ", "_") for p in parts if p.strip())


class Journal:
    """Pending effects of in-memory ports; committed or dropped with the kernel unit of work."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], None]] = []

    def commit(self) -> None:
        for action in self.pending:
            action()
        self.pending.clear()

    def rollback(self) -> None:
        self.pending.clear()


class JournalingFactory:
    def __init__(self, inner: MemoryUowFactory, journal: Journal) -> None:
        self.inner, self.journal = inner, journal

    @contextmanager
    def begin(
        self, *, actor: Actor, command: CommandIdentity | None = None, correlation_id: str
    ) -> Iterator[UnitOfWork]:
        try:
            with self.inner.begin(actor=actor, command=command, correlation_id=correlation_id) as uow:
                yield uow
        except BaseException:
            self.journal.rollback()
            raise
        self.journal.commit()


class FakeLineage:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        self.revisions: dict[str, int] = {}
        self.applied_keys: dict[str, tuple[ConfirmedFinding, ...]] = {}
        self.bindings: dict[str, tuple[str, str]] = {}

    def advance(self, uow: UnitOfWork, *, report_id: str, expected_revision: int, new_revision: int) -> bool:
        if self.revisions.get(report_id) != expected_revision:
            return False
        self.journal.pending.append(lambda: self.revisions.__setitem__(report_id, new_revision))
        return True

    def append(self, uow: UnitOfWork, findings: tuple[ConfirmedFinding, ...], *, command_key: str) -> None:
        self.journal.pending.append(lambda: self.applied_keys.__setitem__(command_key, findings))

    def applied(self, view: object, *, command_key: str) -> bool:
        return command_key in self.applied_keys

    def bind(self, uow: UnitOfWork, *, command_key: str, report_id: str, request_digest: str) -> None:
        self.journal.pending.append(
            lambda: self.bindings.__setitem__(command_key, (report_id, request_digest))
        )

    def binding(self, view: object, *, owner_person_id: str, command_key: str) -> tuple[str, str] | None:
        return self.bindings.get(command_key)

    def findings_of(self, view: object, *, command_key: str) -> tuple[ConfirmedFinding, ...]:
        return self.applied_keys.get(command_key, ())

    def all_findings(self) -> tuple[ConfirmedFinding, ...]:
        return tuple(f for v in self.applied_keys.values() for f in v)


class FakeRecorder:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        self.recorded: list[ConfirmedFinding] = []
        self.fail: Exception | None = None

    def record(self, uow: UnitOfWork, session: SessionContext, finding: ConfirmedFinding) -> str:
        if self.fail is not None:
            raise self.fail
        self.journal.pending.append(lambda: self.recorded.append(finding))
        return "obs_" + finding.finding_id


class FakeStore:
    """The ReportStore port with the coordinator's visible semantics (SYSTEM 5.8.1)."""

    def __init__(self, factory: JournalingFactory, commands: MemoryCommands, lineage: FakeLineage) -> None:
        self.factory, self.commands, self.lineage = factory, commands, lineage
        self.reports: dict[str, Report] = {}
        self.sources: dict[str, bytes] = {}
        self.serve_stale: Report | None = None
        self.deleted: set[str] = set()

    def load(self, session: SessionContext, report_id: str) -> Report | None:
        if report_id in self.deleted:
            return None
        if self.serve_stale is not None and self.serve_stale.report_id == report_id:
            stale, self.serve_stale = self.serve_stale, None
            return stale
        return self.reports.get(report_id)

    def _identity(self, session: SessionContext, key: str, operation: str, target: str) -> CommandIdentity:
        return command_identity(
            key=key,
            actor_ref=session.actor.id,
            operation=operation,
            target=target,
            parameters_json=b"{}",
            body_fingerprint="0" * 64,
            fingerprint_kind="hmac_sha256",
        )

    def _publish(
        self,
        session: SessionContext,
        key: str,
        operation: str,
        report: Report,
        apply: Callable[[UnitOfWork], bool],
        source: bytes | None,
    ) -> PrivateReceipt:
        identity = self._identity(session, key, operation, report.report_id)
        with self.factory.begin(actor=session.actor, command=identity, correlation_id=key) as uow:
            if self.commands.lookup(uow, identity) is None:
                if not apply(uow):
                    raise PrivatePublicationRejected()
                receipt = self.commands.accepted(uow, identity)
                self.commands.record(uow, identity, StoredResult(receipt, 201, report.report_id, "", None))
                self.factory.journal.pending.append(lambda: self._commit(report, source))
        return PrivateReceipt(report.report_id, key, len(self.reports))

    def _commit(self, report: Report, source: bytes | None) -> None:
        self.reports[report.report_id] = report
        self.lineage.revisions[report.report_id] = report.revision
        if source is not None:
            self.sources[report.report_id] = source

    def store_new(
        self,
        session: SessionContext,
        *,
        key: str,
        report: Report,
        source: bytes,
        apply: Callable[[UnitOfWork], bool],
    ) -> PrivateReceipt:
        return self._publish(session, key, "uploadReport", report, apply, source)

    def read_source(self, session: SessionContext, report_id: str) -> bytes | None:
        return None if report_id in self.deleted else self.sources.get(report_id)

    def delete(self, session: SessionContext, *, key: str, report_id: str) -> None:
        self.deleted.add(report_id)

    def publish_version(
        self,
        session: SessionContext,
        *,
        key: str,
        operation: str,
        report: Report,
        apply: Callable[[UnitOfWork], bool],
    ) -> PrivateReceipt:
        return self._publish(session, key, operation, report, apply, None)


class FakePolicies:
    def __init__(self, policies: Mapping[str, FindingPolicy]) -> None:
        self.policies = dict(policies)
        self.by_release: dict[str, dict[str, FindingPolicy]] = {}

    def for_code(self, release: ReleaseHandle, code: str) -> FindingPolicy | None:
        return self.by_release.get(release.release_id, self.policies).get(code)


class FakeForms:
    def __init__(self, forms: Mapping[str, FormDefinition]) -> None:
        self.forms = dict(forms)

    def get(self, release: ReleaseHandle, form_id: str) -> FormDefinition | None:
        return self.forms.get(form_id)


def _settings(max_report: int) -> MemorySettings:
    values: dict[str, int] = {
        "storage.max_rows": 400,
        "storage.max_statement_bytes": 1000,
        "event_bus.max_events": 4,
        "events.max_subscribers": 4,
        "scheduler.max_jobs": 4,
        "commands.cache_entries": 400,
        "commands.max_bytes": 1024,
        "tasks.max_pending": 40,
        "tasks.claim_batch": 4,
        "tasks.max_attempts": 3,
        "tasks.lease_ms": 60000,
        "tasks.max_input_bytes": 1000,
        "tasks.max_result_bytes": 1000,
        "private.max_versions": 100,
        "private.max_entry_bytes": 33554432,
        "private.recovery_batch": 4,
        "private.max_report_bytes": max_report,
    }
    specs = [
        SettingSpec(k, "integer", None, Decimal(1), Decimal(max(v, 33554432)), None, "explicit test profile")
        for k, v in values.items()
    ]
    return MemorySettings(specs, values)


@dataclass
class Station:
    factory: MemoryUowFactory
    journal: Journal
    clock: MemoryClock
    store: FakeStore
    lineage: FakeLineage
    recorder: FakeRecorder
    tasks: MemoryTasks
    commands: MemoryCommands
    policies: FakePolicies
    forms: FakeForms
    workflows: ReportWorkflows
    limits: ReportLimits
    ports: ReportPorts
    sequence: list[int] = field(default_factory=lambda: [0])

    def upload(
        self, data: bytes = PDF, *, key: str = "up1", who: SessionContext | None = None, **kw: object
    ) -> Report:
        args: dict[str, object] = {
            "data": data,
            "original_name": "report.pdf",
            "declared_type": "application/pdf",
            "access_scope": None,
            "examination_date": "2026-09-30",
            "report_date": "2026-09-30",
            "origin_mode": "SIM",
        }
        args.update(kw)
        return self.workflows.upload(who or session(), key=key, **args)  # type: ignore[arg-type]

    def lease(
        self, report: Report, *, handler_version: str = "extract-1", release_id: str = "release"
    ) -> TaskLease:
        """Submit and claim one report_extraction task pinned to the report source version."""
        from crew_health.reports.domain.extraction import pin_ref

        self.sequence[0] += 1
        n = str(self.sequence[0])
        identity = command_identity(
            key="task" + n,
            actor_ref=OWNER,
            operation="extract",
            target=report.report_id,
            parameters_json=b"{}",
            body_fingerprint="1" * 64,
            fingerprint_kind="hmac_sha256",
        )
        task = TaskInput(
            "task" + n,
            "report_extraction",
            handler_version,
            pin_ref(report.report_id, report.source_version_id),
            "2" * 64,
            release_id,
            None,
            Actor("person", report.patient_id),
            Audience("owner", report.report_id, AudienceProjectionKey("reports", "report", 1), "policy"),
            identity,
            "report_extraction",
        )
        with self.factory.begin(actor=Actor("engine", "worker"), correlation_id="claim" + n) as uow:
            self.tasks.submit(uow, task)
            return self.tasks.claim(
                uow, worker_id="worker", handlers=frozenset({"report_extraction"}), limit=1
            )[0]

    def activate_release(self, release_id: str, policies: Mapping[str, FindingPolicy]) -> None:
        """A new configuration release becomes active; earlier releases stay resolvable."""
        self.ports.releases.releases[release_id] = MemoryRelease(release_id, {})  # type: ignore[attr-defined]
        self.ports.releases.active_id = release_id  # type: ignore[attr-defined]
        self.policies.by_release[release_id] = dict(policies)

    def cancel(self, lease: TaskLease) -> None:
        with self.factory.begin(actor=Actor("person", OWNER), correlation_id="cancel") as uow:
            self.tasks.cancel(uow, session(), lease.task.task_id)


def station(
    *,
    max_report: int = MAX_REPORT,
    policies: Mapping[str, FindingPolicy] | None = None,
    forms: Mapping[str, FormDefinition] | None = None,
) -> Station:
    settings = _settings(max_report)
    clock = MemoryClock(AT, 0, "epoch")
    ids = MemoryIds(identifiers=tuple(str(i) for i in range(5000)))
    release = MemoryRelease("release", {})
    config = MemoryConfig([release], active_id="release")
    inner = MemoryUowFactory(
        clock=clock,
        ids=ids,
        release=release,
        settings=settings,
        connection=MemoryStorage(settings=settings, responses={}, results={}),
        node_id="node",
        incarnation="incarnation",
    )
    journal = Journal()
    factory = JournalingFactory(inner, journal)
    commands = MemoryCommands(
        ids=ids,
        settings=settings,
        authorize=lambda view, session, identity: session.actor.id == identity.actor_ref,
        operations={},
    )
    lineage = FakeLineage(journal)
    recorder = FakeRecorder(journal)
    store = FakeStore(factory, commands, lineage)
    handler = MemoryTaskHandler(
        lambda lease: TaskResult(lease.task.task_id, lease.generation, "ref", "3" * 64),
        lambda uow, lease, result: None,
        lambda result: None,
    )
    tasks = MemoryTasks(
        settings=settings,
        registrations=[
            TaskRegistration("report_extraction", "extract-1", handler, None, "report_extraction"),
            TaskRegistration("report_extraction", "extract-2", handler, None, "report_extraction"),
        ],
        authorize=lambda view, session, task: session.person_id == task.actor.id,
        capture=lambda at: TimeInput(at, "source", "segment", 1, "source_raw", "SIM"),
        elapsed=lambda view, start, end: end.original_at - start.original_at,
    )
    limits = ReportLimits(max_drafts=50, max_value_chars=200)
    fake_policies = FakePolicies(policies or {})
    fake_forms = FakeForms(forms or {})
    ports = ReportPorts(
        store=store,
        lineage=lineage,
        recorder=recorder,
        policies=fake_policies,
        forms=fake_forms,
        units=UNITS,
        tasks=tasks,
        uow=factory,
        snapshots=inner,
        idempotency=commands,
        receipts=commands,
        releases=config,
        ids=ids,
        clock=clock,
        settings=settings,
        limits=limits,
    )
    return Station(
        inner,
        journal,
        clock,
        store,
        lineage,
        recorder,
        tasks,
        commands,
        fake_policies,
        fake_forms,
        ReportWorkflows(ports),
        limits,
        ports,
    )


def form_from_dictionary(
    field_id: str,
    *,
    result: FindingPolicy | None,
    interpretation: FindingPolicy,
    origins: frozenset[str] = frozenset({"clinician", "device"}),
    reference_interval: bool = False,
) -> FormDefinition:
    """A specialist form whose id, family and required attributes come from the input dictionary row."""
    row = dictionary_row(field_id)
    fields = []
    if result is not None:
        fields.append(FieldDefinition("result", result, "result", True, reference_interval))
    fields.append(FieldDefinition("interpretation", interpretation, "interpretation", False, False))
    return FormDefinition(
        row["field_id"],
        row["group"],
        "test-1",
        "docs/product/input-dictionary.csv",
        tuple(fields),
        required_attributes(row),
        origins,  # type: ignore[arg-type]
    )


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def with_status(report: Report, **changes: object) -> Report:
    return replace(report, **changes)  # type: ignore[arg-type]
