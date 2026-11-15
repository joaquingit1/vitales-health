"""The real-storage station for T2/T3: a real SQLite hub, the real E01 private coordinator and task service, the
real report adapters, router and extraction runner. Only the private store, the vault and the key reader are
E01/E02 kernel fakes (no live vault backend exists on main, R-E02-20); the fault store below injects the
crash points of SYSTEM 5.8.1 on top of them. Never a production fallback."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from crew_health.app.errors import to_envelope
from crew_health.contracts_gen.enums import ConfigName, Role
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.private import PrivateReceipt, SealedPrivateRef
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.tasks import TaskRegistration
from crew_health.kernel.testing import (
    MemoryClock,
    MemoryConfig,
    MemoryPrivateStore,
    MemoryRelease,
    MemorySettings,
    MemoryVault,
)
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.platform.adapters.intents import IntentRepository
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.adapters.private import PrivateReferences
from crew_health.platform.adapters.tasks import TaskRepository
from crew_health.platform.adapters.units import UcumUnitRegistry
from crew_health.platform.application.health import ComponentHealthState
from crew_health.platform.application.private import PrivateCoordinator
from crew_health.platform.application.tasks import TaskService
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database
from crew_health.reports.adapters.contained import ContainedParser, ParserBounds, ParserUnavailable
from crew_health.reports.adapters.index import ReportIndex
from crew_health.reports.adapters.lineage import SqliteConfirmationLineage
from crew_health.reports.adapters.parsing import ParseLimits
from crew_health.reports.adapters.policies import ConfigFindingPolicies, ConfigVocabulary
from crew_health.reports.adapters.recorder import DeferredObservationRecorder
from crew_health.reports.adapters.store import PrivateReportStore, PrivateServices, StoreLimits
from crew_health.reports.adapters.writer import ReportReferenceWriter
from crew_health.reports.api.router import ReportsApi, build_router
from crew_health.reports.application.extraction import ENGINE_VERSION, ExtractionRunner, ParsedFile
from crew_health.reports.application.workflows import EXTRACTION_TASK, ReportPorts, ReportWorkflows
from crew_health.reports.domain.extraction import ReportLimits
from crew_health.reports.domain.media import QuarantineReason
from crew_health.reports.module import setting_specs

ROOT = Path(__file__).resolve().parents[3]
AT = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
OWNER, OTHER, OFFICER = "mara", "niko", "doc"
MIGRATION = ROOT / "hub/src/crew_health/reports/adapters/migrations/0001_reports.sql"
REPORT_VALUES = {
    "reports.max_pages": 20,
    "reports.max_pixels": 4000000,
    "reports.max_text_chars": 200000,
    "reports.max_decoded_bytes": 2000000,
    "reports.max_objects": 5000,
    "reports.parser_timeout_ms": 20000,
    "reports.parser_max_output_bytes": 1000000,
    "reports.max_drafts": 50,
    "reports.max_value_chars": 200,
    "reports.max_entry_attempts": 4,
    "reports.extraction_batch": 4,
}


def person(who: str = OWNER, *, roles: frozenset[Role] = frozenset({Role.CREW})) -> SessionContext:
    return SessionContext("session_" + who, "crew", Actor("person", who), who, roles)


def release_documents(*, catalog: dict[str, object] | None = None) -> dict[ConfigName, ConfigDocumentView]:
    """The release's documents: the repository's unit mapping and the example indicator catalog."""
    mapping = (ROOT / "config/unit-mapping.json").read_bytes()
    example = json.loads(
        (ROOT / "docs/product/api/examples/config/indicator-catalog.example.json").read_text("utf-8")
    )
    raw = canonical_json(catalog if catalog is not None else example["value"])
    from hashlib import sha256

    return {
        ConfigName.UNIT_MAPPING: ConfigDocumentView(
            ConfigName.UNIT_MAPPING, sha256(mapping).hexdigest(), mapping
        ),
        ConfigName.INDICATOR_CATALOG: ConfigDocumentView(
            ConfigName.INDICATOR_CATALOG, sha256(raw).hexdigest(), raw
        ),
    }


class FaultStore(MemoryPrivateStore):
    """The kernel's private store with the crash points of SYSTEM 5.8.1 as one-shot faults."""

    def __init__(self, *, vault: MemoryVault, settings: MemorySettings) -> None:
        super().__init__(vault=vault, settings=settings)
        self.fail_seal = False
        self.fail_ack = False
        self.after_seal: Callable[[SealedPrivateRef], None] | None = None

    def seal(self, intent):  # type: ignore[no-untyped-def]
        if self.fail_seal:
            self.fail_seal = False
            raise StorageUnavailable("disk_full")
        reference = super().seal(intent)
        if self.after_seal is not None:
            hook, self.after_seal = self.after_seal, None
            hook(reference)
        return reference

    def acknowledge(self, reference: SealedPrivateRef, receipt: PrivateReceipt) -> None:
        if self.fail_ack:
            self.fail_ack = False
            raise StorageUnavailable("disk_full")
        super().acknowledge(reference, receipt)


class Reader:
    def __init__(self, store: FaultStore, vault: MemoryVault) -> None:
        self.store, self.vault = store, vault

    def read(self, session: SessionContext, reference: SealedPrivateRef) -> bytes:
        saved = self.store.entries.get(reference.entry_id)
        if saved is None or saved[1] != reference:
            raise StorageUnavailable("corrupt")
        return self.vault.decrypt(
            session,
            reference=reference,
            blob=saved[2],
            associated_data=canonical_json(
                [reference.owner_person_id, reference.entry_id, reference.version_id]
            ),
        )


class Routing:
    def record_origin(self, person_id: str) -> str:
        return "SIM"


def parse_in_process(kind: str, data: bytes, vocabulary: dict[str, dict[str, object]]) -> ParsedFile:
    from crew_health.reports.adapters import worker

    header = {
        "op": "extract",
        "kind": kind,
        "vocabulary": vocabulary,
        "max_proposals": 50,
        "limits": {
            "max_pages": 20,
            "max_pixels": 4000000,
            "max_text_chars": 200000,
            "max_decoded_bytes": 2000000,
            "max_objects": 5000,
        },
    }
    out = worker.handle(header, data)
    if not out["ok"]:
        return ParsedFile(False, str(out["reason"]), (), ())
    return ParsedFile(
        True,
        None,
        tuple(int(n) for n in out["pages"]),  # type: ignore[attr-defined]
        tuple((p[0], p[1], p[2], p[3], p[4], p[5]) for p in out["proposals"]),  # type: ignore[attr-defined]
    )


@dataclass
class Rig:
    tmp: Path
    clock: MemoryClock
    db: SQLiteDatabase
    vault: MemoryVault
    store_faults: FaultStore
    coordinator: PrivateCoordinator
    tasks: TaskService
    workflows: ReportWorkflows
    store: PrivateReportStore
    runner: ExtractionRunner
    client: TestClient
    api: ReportsApi
    releases: MemoryConfig
    settings: MemorySettings
    parse_calls: list[str] = field(default_factory=list)

    def get(self, who: str, path: str, **kw: object) -> object:
        return self.client.get(path, headers={"X-Person": who}, **kw)  # type: ignore[arg-type]

    def upload(
        self,
        data: bytes,
        *,
        key: str,
        who: str = OWNER,
        name: str = "report.pdf",
        ctype: str = "application/pdf",
        **fields: str,
    ):  # type: ignore[no-untyped-def]
        return self.client.post(
            "/v1/reports",
            headers={"X-Person": who, "Idempotency-Key": key},
            files={"file": (name, data, ctype)},
            data=fields,
        )

    def confirm(self, who: str, report_id: str, body: dict[str, object], *, key: str, etag: str | None):  # type: ignore[no-untyped-def]
        headers = {"X-Person": who, "Idempotency-Key": key}
        if etag is not None:
            headers["If-Match"] = etag
        return self.client.post(f"/v1/reports/{report_id}/confirmations", headers=headers, json=body)

    def crash_and_restart(self) -> int:
        """Process restart: every in-memory piece is dropped except what is durable (hub file, sealed store)."""
        return self.coordinator.recover(limit=self.db.settings.integer("private.recovery_batch"))


def build(tmp: Path, *, in_process_parser: bool = True, catalog: dict[str, object] | None = None) -> Rig:
    clock = MemoryClock(AT, 0, "epoch_1")
    db = database(tmp, clock=clock)
    db.migrate(MIGRATION.read_text(encoding="utf-8"))
    from crew_health.reports.module import audit_policies

    for policy in audit_policies():
        db.register_audit_policy(policy)
    private = {
        "private.max_report_bytes": 20000000,
        "private.max_versions": 100,
        "private.max_entry_bytes": 33554432,
    }
    specs = [
        *setting_specs(),
        *(
            SettingSpec(k, "integer", None, Decimal(1), Decimal(v), None, "SYSTEM 5.11")
            for k, v in private.items()
        ),
    ]
    settings = MemorySettings(specs, {**REPORT_VALUES, **private})
    vault = MemoryVault(
        ids=db.ids,
        settings=db.settings,
        secrets={OWNER: b"owner-key", OTHER: b"other-key", OFFICER: b"doc-key"},
        authorize=lambda session, ref: session.person_id == ref.owner_person_id and session.kind == "crew",
    )
    faults = FaultStore(vault=vault, settings=db.settings)
    ledger = CommandLedger(db.ids)
    coordinator = PrivateCoordinator(
        transactions=db,
        snapshots=db,
        store=faults,
        vault=vault,
        writers={"report": ReportReferenceWriter()},
        ledger=ledger,
        intents=IntentRepository(ledger),
        references=PrivateReferences(),
        ids=db.ids,
        settings=db.settings,
        authorize=lambda s, owner: s.person_id == owner,
        health=ComponentHealthState(maximum=64),
        clock=clock,
    )
    from crew_health.reports.module import _NoopHandler

    handler = _NoopHandler()
    tasks = TaskService(
        settings=db.settings,
        repository=TaskRepository(),
        ids=db.ids,
        registrations=(TaskRegistration(EXTRACTION_TASK, ENGINE_VERSION, handler, None, None),),
        authorize=lambda view, session, task: session.actor == task.actor,
        authorize_completion=lambda uow, task: task.actor.kind == "person",
        capture=lambda at: TimeInput(at, "source", "segment", 1, "source_raw", "SIM"),
        elapsed=lambda view, first, second: second.original_at - first.original_at,
    )
    releases = MemoryConfig(
        [MemoryRelease("release_1", release_documents(catalog=catalog))], active_id="release_1"
    )
    index = ReportIndex()
    store = PrivateReportStore(
        services=PrivateServices(coordinator, faults, vault, Reader(faults, vault)),
        index=index,
        snapshots=db,
        ids=db.ids,
        limits=StoreLimits(4, 100, 33554432),
    )
    bounds = ParserBounds(ParseLimits(20, 4000000, 200000, 2000000, 5000), 20000, 1000000, 50)
    contained = ContainedParser(bounds)

    def inspect(kind: str, data: bytes) -> QuarantineReason | None:
        try:
            out = contained.run("inspect", kind, data, {})
        except ParserUnavailable:
            return "unverified"
        return None if out.ok else ("over_bounds" if out.reason == "too_large" else "corrupt")

    ports = ReportPorts(
        store=store,
        lineage=SqliteConfirmationLineage(),
        recorder=DeferredObservationRecorder(),
        policies=ConfigFindingPolicies(),
        forms=_NoForms(),  # type: ignore[arg-type]
        units=UcumUnitRegistry(max_cached=64),
        tasks=tasks,
        uow=db,
        snapshots=db,
        idempotency=ledger,
        receipts=ledger,
        releases=releases,
        ids=db.ids,
        clock=clock,
        settings=settings,
        limits=ReportLimits(50, 200),
        inspector=inspect,
        queue=tasks,
        extraction_version=ENGINE_VERSION,
    )
    workflows = ReportWorkflows(ports)
    parse = parse_in_process if in_process_parser else _contained(contained)
    runner = ExtractionRunner(
        workflows=workflows,
        runner=tasks,
        uows=db,
        releases=releases,
        parse=parse,
        vocabulary=ConfigVocabulary().of,
        batch=4,
    )
    api = ReportsApi(workflows, Routing().record_origin, None)
    app = FastAPI()

    @app.middleware("http")
    async def sessions(request: Request, call_next):  # type: ignore[no-untyped-def]
        who = request.headers.get("X-Person")
        if who is not None:
            roles = frozenset({Role.CREW_MEDICAL_OFFICER}) if who == OFFICER else frozenset({Role.CREW})
            request.state.session = person(who, roles=roles)
        return await call_next(request)

    @app.exception_handler(ProblemError)
    async def problem(request: Request, error: ProblemError) -> JSONResponse:
        return JSONResponse(to_envelope(error, "corr_test").model_dump(mode="json"), status_code=error.status)

    app.include_router(build_router(api))
    rig = Rig(
        tmp,
        clock,
        db,
        vault,
        faults,
        coordinator,
        tasks,
        workflows,
        store,
        runner,
        TestClient(app),
        api,
        releases,
        settings,
    )
    return rig


def _contained(
    contained: ContainedParser,
) -> Callable[[str, bytes, dict[str, dict[str, object]]], ParsedFile]:
    def parse(kind: str, data: bytes, vocabulary: dict[str, dict[str, object]]) -> ParsedFile:
        try:
            out = contained.run("extract", kind, data, vocabulary)
        except ParserUnavailable:
            return ParsedFile(False, "parser_unavailable", (), ())
        return ParsedFile(out.ok, out.reason, out.page_text_lengths, out.proposals)

    return parse


class _Noop:
    def execute(self, lease):  # type: ignore[no-untyped-def]
        raise ProblemError  # never called: the runner parses outside the unit of work

    def apply(self, uow, lease, result) -> None:  # type: ignore[no-untyped-def]
        return

    def discard(self, result) -> None:  # type: ignore[no-untyped-def]
        return


class _NoForms:
    def get(self, release: object, form_id: str) -> None:
        return None
