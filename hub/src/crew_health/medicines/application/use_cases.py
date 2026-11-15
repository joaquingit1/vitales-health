"""Medicines use cases (E32 4.4). The engine proposes; the crew medical officer, or the acting medical officer for an
`any_trained_crew` product or a medicine the subject's active plan names (Q14), confirms. Every safety limit is rechecked inside the confirming
transaction; stock draws go through the supplies stock port, so a lot never goes below zero."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.audit import AuditEntry, AuditPolicy
from crew_health.kernel.clock import Clock, ClockCorrections
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    IdempotencyStore,
    StoredResult,
    command_identity,
)
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.tasks import TaskQueue
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext, UnitOfWork, UnitOfWorkFactory
from crew_health.medicines.application import events, scheduling
from crew_health.medicines.application.codec import (
    amount_json,
    context_part,
    instant,
    parse_instant,
    proposal_wire,
    summary_from,
)
from crew_health.medicines.application.ports import (
    BundleCheck,
    ClockCheck,
    ConsentQueries,
    MedicinesRepository,
    Movement,
    Partition,
    PlanMedicines,
    PotencyEngine,
    PotencyStore,
    RecordRow,
    View,
    WeightReader,
)
from crew_health.medicines.domain.documents import activation_findings
from crew_health.medicines.domain.eligibility import Confirmer, may_confirm, may_override, may_record_manual
from crew_health.medicines.domain.engine import exclusions, fresh, manual_findings, propose
from crew_health.medicines.domain.model import (
    DoseContext,
    DoseRequest,
    FormularyProduct,
    LotSummary,
    LotView,
    MedicationContext,
    MedicinesPolicy,
    Mode,
    PastAdministration,
    Proposal,
    Treatments,
    WorkingDiagnosis,
)
from crew_health.medicines.domain.units import Amount
from crew_health.roles.application.ports import ClinicalAuthorityQuery
from crew_health.supplies.application.ports import (
    MissionStatePort,
    StockBelowZero,
    StockLevel,
    StockUnitMismatch,
    drawn,
)

CMO = "crew_medical_officer"
SCOPE = "medicines"  # ConsentQueries scope (SCOPE, field): context, proposals, administrations, names
PARTS = ("allergies", "other_medicines", "renal", "hepatic", "conditions")
AUDIT_ADMINISTERED = "medicines.administered"
AUDIT_ADMINISTERED_ACTING = (
    "medicines.administered_acting"  # refs the role_assignment that made the confirmer acting
)
AUDIT_CORRECTED = "medicines.corrected"
CONFIRMED_AS: frozenset[str | int | bool] = frozenset({"crew_medical_officer", "acting_medical_officer"})
ADMIN_RECORD = "medication_administration"
_INTERNAL = frozenset({"rule_id", "lineage_id", "reason_text"})  # stored, never on the wire


@dataclass(frozen=True, slots=True)
class Documents:
    treatments: Treatments
    policy: MedicinesPolicy
    formulary: Mapping[str, FormularyProduct]
    lots: tuple[Mapping[str, object], ...]  # lots.json entries of the active pharmacy bundle
    condition_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class Limits:
    max_rows: int
    max_window_rows: int
    future_tolerance_seconds: int
    clock_backward_tolerance_seconds: int
    clock_forward_tolerance_seconds: int = 60
    retained_unreferenced_runs: int = 20
    max_inputs_bytes: int = 128 * 1024 * 1024
    max_weights: int = 8


@dataclass(frozen=True, slots=True)
class Deps:
    uow: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    ids: IdFactory
    idempotency: IdempotencyStore
    repository: MedicinesRepository
    mission: MissionStatePort
    authority: ClinicalAuthorityQuery | None
    documents: Callable[[], Documents]
    limits: Limits
    process_epoch: str
    store: PotencyStore
    engine: PotencyEngine
    bundle_check: BundleCheck
    tasks: TaskQueue | None
    weights: WeightReader | None
    clock: Clock | None = None
    corrections: ClockCorrections | None = None
    consent: ConsentQueries | None = None
    plans: PlanMedicines | None = None


@dataclass(frozen=True, slots=True)
class Command:
    key: str
    body_sha256: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class Result:
    status: int
    resource_id: str | None
    body: Mapping[str, object]


def _problem(code: ErrorCode, field: str, message: str, **extra: object) -> ProblemError:
    return ProblemError(code, message, field_errors=(FieldError(field, code.value, message),), **extra)  # type: ignore[arg-type]


def _partition(deps: Deps, view: View) -> Partition:
    facts = deps.mission.current(view)
    if facts is None:
        raise ProblemError(ErrorCode.CONTEXT_INAPPLICABLE, "no current mission", retryable=False)
    return Partition(facts.mission_id, facts.mission_mode, facts.active_environment_id)


def _confirmer(deps: Deps, view: View, session: SessionContext, subject: str) -> Confirmer:
    """E31's clinical authority for this subject now (SYSTEM 8.4), never a caller boolean. Without E31 the crew
    medical officer role of the authenticated session stands and nobody is acting."""
    if session.person_id is None or session.kind == "station":
        return Confirmer(None, "none")
    if deps.authority is None:
        roles = {str(r) for r in session.roles}
        return Confirmer(session.person_id, "crew_medical_officer" if CMO in roles else "none")
    decided = deps.authority.authority(
        view,
        person_id=session.person_id,
        subject_person_id=subject,
        action="dose_confirmation",
        dose=None,
        at=view.now,
    )
    return Confirmer(session.person_id, decided.kind, decided.role_assignment_id)


def _require_clinician(deps: Deps, view: View, session: SessionContext, subject: str) -> Confirmer:
    who = _confirmer(deps, view, session, subject)
    if not may_record_manual(who, subject):
        raise _problem(
            ErrorCode.ROLE_NOT_ELIGIBLE,
            "/",
            "the crew medical officer or the acting officer, not the subject",
        )
    return who


def _can_read(deps: Deps, view: View, session: SessionContext, subject: str, field: str) -> bool:
    """SYSTEM 8.3 object and field grant: the subject, or a reader the subject shared this field with (E02 consent).
    Officer status, an acting assignment or a help session is never a chart grant; a station never reads."""
    if session.person_id is None or session.kind == "station":
        return False
    if session.person_id == subject:
        return True
    return deps.consent is not None and deps.consent.allows(
        view, subject_person_id=subject, recipient=session.person_id, scope=(SCOPE, field)
    )


def _require_read(deps: Deps, view: View, session: SessionContext, subject: str, field: str) -> None:
    if not _can_read(deps, view, session, subject, field):
        raise _problem(ErrorCode.UNAUTHORIZED_SCOPE, "/", "no grant for this crew member's " + field)


_FIELD_OF_KIND = {
    "context": "context",
    "diagnosis": "context",
    "proposal": "proposals",
    "administration": "administrations",
}


class MedicinesResultVisibility:
    """kernel ResultVisibility for a saved medicines result (SYSTEM 5.8): rechecked under the current grant before a
    replay returns it or even its reference; a record with no subject (lot, movement, assessment) is crew data."""

    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    def authorize(
        self, snapshot: View, session: SessionContext, identity: CommandIdentity, result: StoredResult
    ) -> None:
        if result.resource_ref is None:
            return
        row = self.deps.repository.record(snapshot, result.resource_ref)
        subject = None if row is None else row.subject_person_id
        if row is None or subject is None:
            return
        _require_read(self.deps, snapshot, session, subject, _FIELD_OF_KIND.get(row.kind, "context"))


_HEAD_PAGES = 64


def _correction_head(deps: Deps, view: View) -> int | None:
    """The newest clock correction revision (SYSTEM 5.2 global journal), paged with a bound. None when the journal
    does not end within the bound: the head is then unknown, and a jump detected with it stays marked (fail closed),
    because any revision taken as the head could predate corrections that were recorded before the jump."""
    if deps.corrections is None:
        return 0
    head = 0
    for _ in range(_HEAD_PAGES):
        page = deps.corrections.history(view, after_version=head, limit=deps.limits.max_rows)
        if page:
            head = max(c.version for c in page)
        if len(page) < deps.limits.max_rows:
            return head
    return None


def _clock_marked(deps: Deps, view: View) -> bool:
    """C4: a forward discontinuity stays marked until a person records a clock correction after it (a correction
    revision newer than the journal head at detection). Without the clock module, or when the head was unknown at
    detection, nothing can tell a later correction from an earlier one, so it stays marked."""
    mark = deps.repository.last_clock_check(view, "discontinuity")
    if mark is None:
        return False
    if deps.corrections is None or mark.correction_version is None:
        return True
    later = deps.corrections.history(view, after_version=mark.correction_version, limit=1)
    return not later


def _clock_check(deps: Deps, uow: UnitOfWork) -> None:
    """C1 and C3, once per medicines command: compare wall against monotonic time since this process's last
    medicines operation; a forward jump beyond the tolerance is stored as evidence. Then this reading is stored."""
    if deps.clock is None:
        return
    monotonic = deps.clock.monotonic_ns()
    last = deps.repository.last_clock_check(uow, "reading", deps.process_epoch)
    if last is not None:
        wall_ms = int((uow.now - last.at).total_seconds() * 1000)
        monotonic_ms = (monotonic - last.monotonic_ns) // 1_000_000
        if wall_ms - monotonic_ms > deps.limits.clock_forward_tolerance_seconds * 1000:
            deps.repository.add_clock_check(
                uow,
                ClockCheck(
                    0,
                    "discontinuity",
                    uow.now,
                    monotonic,
                    deps.process_epoch,
                    last.seq,
                    wall_ms,
                    monotonic_ms,
                    _correction_head(deps, uow),
                ),
            )
    deps.repository.add_clock_check(uow, ClockCheck(0, "reading", uow.now, monotonic, deps.process_epoch))


def _future(deps: Deps, at: datetime, now: datetime, field: str) -> None:
    if at.tzinfo is None or at > now + timedelta(seconds=deps.limits.future_tolerance_seconds):
        raise _problem(ErrorCode.INVALID_VALUE, field, "a time later than the hub clock is refused (C6)")


def _identity(
    session: SessionContext,
    cmd: Command,
    operation: str,
    target: str,
    partition: Partition,
    extra: Mapping[str, str],
) -> CommandIdentity:
    """SYSTEM 5.8: every semantically relevant header is a parameter; `If-Match` (the revision a confirmation was
    made against) is one, so the same key with another revision is a conflict, never a replay."""
    parameters = {
        "target": target,
        "mission_id": partition.mission_id,
        "origin_mode": partition.origin_mode,
        **extra,
    }
    return command_identity(
        key=cmd.key,
        actor_ref=session.actor.id,
        operation=operation,
        target=target,
        parameters_json=json.dumps(parameters, sort_keys=True).encode(),
        body_fingerprint=cmd.body_sha256,
        fingerprint_kind="sha256",
    )


def _command_key(identity: CommandIdentity) -> str:
    return f"api:{identity.actor_ref}:{identity.operation}:{identity.key}"


def _run_command(
    deps: Deps,
    session: SessionContext,
    cmd: Command,
    operation: str,
    target: str,
    apply: Callable[[UnitOfWork, Partition, str], Result],
    replay: Callable[[UnitOfWork, str], Mapping[str, object]],
    parameters: Mapping[str, str] | None = None,
) -> Result:
    """Identity first, then the ledger (a saved result is returned only under the current grant), then the clock
    check and the business checks, in one BEGIN IMMEDIATE unit of work."""
    with deps.snapshots.snapshot() as view:
        partition = _partition(deps, view)
    identity = _identity(session, cmd, operation, target, partition, parameters or {})
    with deps.uow.begin(actor=session.actor, command=identity, correlation_id=cmd.correlation_id) as uow:
        stored = deps.idempotency.lookup(uow, identity)
        if stored is not None:
            if stored.resource_ref is None:
                raise ProblemError(ErrorCode(stored.result_code), "refused before", retryable=False)
            MedicinesResultVisibility(deps).authorize(uow, session, identity, stored)
            return Result(stored.status, stored.resource_ref, replay(uow, stored.resource_ref))
        now_partition = _partition(deps, uow)
        if now_partition != partition:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "the mission or mode changed; retry", retryable=True)
        _clock_check(deps, uow)
        result = apply(uow, partition, _command_key(identity))
        receipt = AcceptedReceipt(
            identity.key, identity.operation, identity.target, deps.ids.new("receipt"), uow.now
        )
        deps.idempotency.record(
            uow, identity, StoredResult(receipt, result.status, result.resource_id, "accepted", None)
        )
        return result


def _body(deps: Deps, view: View, record_id: str) -> Mapping[str, object]:
    row = deps.repository.record(view, record_id)
    if row is None:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "stored medicines record missing", retryable=False)
    return row.body


# ---- reads that build the dose context ----


def _current_zone(lot: Mapping[str, object], now: datetime) -> str | None:
    for entry in cast(list[Mapping[str, object]], lot.get("custody") or []):
        start = parse_instant(entry["from_utc"])
        end = entry.get("to_utc")
        if start <= now and (end is None or now < parse_instant(end)):
            return str(entry["zone_id"])
    return None


def latest_summaries(
    deps: Deps, view: View, partition: Partition
) -> tuple[str | None, Mapping[str, LotSummary]]:
    """The lots of the partition's latest completed assessment (E32 DD9: a failed or timed out run never replaces
    it); none before the first one completes."""
    run = deps.store.latest_completed(view, partition.mission_id, partition.origin_mode)
    result = None if run is None else deps.store.result(view, run.run_id)
    if run is None or result is None:
        return None, {}
    lots = cast(Mapping[str, Mapping[str, object]], json.loads(result.summaries_json))
    return run.run_id, {k: summary_from(v) for k, v in lots.items()}


def lot_views(deps: Deps, view: View, partition: Partition, docs: Documents) -> tuple[LotView, ...]:
    zones = set(docs.policy.environment_zones.get(partition.environment_id, ()))
    net = deps.repository.net_movements(view, partition, limit=deps.limits.max_rows)
    adverse = {
        row.lot_id
        for row in deps.repository.records(view, "inspection", partition, limit=deps.limits.max_rows)
        if row.body.get("finding") != "no_change"
    }
    _, summaries = latest_summaries(deps, view, partition)
    out = []
    for lot in docs.lots:
        lot_id = str(lot["lot_id"])
        if _current_zone(lot, view.now) not in zones:
            continue
        quantity = cast(Mapping[str, object], lot["quantity"])
        out.append(
            LotView(
                lot_id=lot_id,
                product_id=str(lot["product_id"]),
                lot_status=str(lot.get("status") or "active"),
                labeled_expiry=parse_instant(lot["labeled_expiry_utc"]),
                quantity_now=Decimal(str(quantity["value"])) + net.get(lot_id, Decimal(0)),
                adverse_inspection=lot_id in adverse,
                summary=summaries.get(lot_id),
            )
        )
    return tuple(out)


def _context(
    deps: Deps, view: View, partition: Partition, subject: str
) -> tuple[MedicationContext, tuple[str, ...]]:
    latest: dict[str, RecordRow] = {}
    for row in deps.repository.records(
        view, "context", partition, limit=deps.limits.max_rows, subject_person_id=subject
    ):
        latest[str(row.body["part"])] = row
    parts = {name: context_part(row.body, row.record_id, row.at) for name, row in latest.items()}
    context = MedicationContext(
        allergies=parts.get("allergies"),
        other_medicines=parts.get("other_medicines"),
        renal=parts.get("renal"),
        hepatic=parts.get("hepatic"),
        conditions=parts.get("conditions"),
    )
    return context, tuple(sorted(row.record_id for row in latest.values()))


def _history(deps: Deps, view: View, partition: Partition, subject: str) -> tuple[PastAdministration, ...]:
    out = []
    rows = deps.repository.records(
        view, "administration", partition, limit=deps.limits.max_rows, subject_person_id=subject
    )
    for row in rows:
        if deps.repository.correction(view, row.record_id) is not None:
            continue
        amounts = deps.repository.ingredients(view, row.record_id)
        out.append(
            PastAdministration(
                administration_id=row.record_id,
                at=parse_instant(row.body["administered_at"]),
                rule_id=cast(str | None, row.body.get("rule_id")),
                lineage_id=cast(str | None, row.body.get("lineage_id")),
                accounting={k: Amount(v, u) for k, (v, u) in amounts.items()},
            )
        )
    return tuple(out)


def _diagnosis(deps: Deps, view: View, partition: Partition, diagnosis_id: str) -> RecordRow:
    row = deps.repository.record(view, diagnosis_id)
    if (
        row is None
        or row.kind != "diagnosis"
        or (row.mission_id, row.origin_mode)
        != (
            partition.mission_id,
            partition.origin_mode,
        )
    ):
        raise _problem(ErrorCode.NOT_FOUND, "/working_diagnosis_id", "working diagnosis not found")
    return row


def _discontinuity(deps: Deps, now: datetime, rows: Sequence[datetime]) -> bool:
    """C2: a stored medicines time later than the hub clock beyond the tolerance marks a discontinuity."""
    return any(at > now + timedelta(seconds=deps.limits.clock_backward_tolerance_seconds) for at in rows)


@dataclass(frozen=True, slots=True)
class Assembled:
    context: DoseContext
    fingerprint: str


def assemble(
    deps: Deps, view: View, partition: Partition, docs: Documents, subject: str, product_id: str
) -> Assembled:
    """E32 4.3 DoseContext in one read, and its canonical hash (`context_fingerprint`)."""
    lots = lot_views(deps, view, partition, docs)
    context, statement_ids = _context(deps, view, partition, subject)
    history = _history(deps, view, partition, subject)
    weights = (
        ()
        if deps.weights is None
        else deps.weights.weights(
            view,
            subject,
            mission_id=partition.mission_id,
            origin_mode=partition.origin_mode,
            limit=deps.limits.max_weights,
        )
    )
    ctx = DoseContext(
        now=view.now,
        mode=cast(Mode, partition.origin_mode),
        treatments=docs.treatments,
        policy=docs.policy,
        formulary=docs.formulary,
        lots=lots,
        history=history,
        context=context,
        weights=weights,
        clock_discontinuity=_discontinuity(deps, view.now, [h.at for h in history])
        or _clock_marked(deps, view),
        max_window_rows=deps.limits.max_window_rows,
    )
    material = {
        "mode": partition.origin_mode,
        "treatments": docs.treatments.sha256,
        "policy": docs.policy.sha256,
        "lots": sorted(
            [
                lot.lot_id,
                str(lot.quantity_now),
                lot.lot_status,
                lot.adverse_inspection,
                None if lot.summary is None else lot.summary.run_id,
                list(exclusions(lot, view.now)),
            ]
            for lot in lots
            if lot.product_id == product_id
        ),
        "history": sorted(h.administration_id for h in history),
        "context": list(statement_ids),
        "weights": [w.observation_id for w in weights],
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()
    return Assembled(ctx, digest)


# ---- commands and reads ----


def record_inspection(
    deps: Deps,
    session: SessionContext,
    cmd: Command,
    lot_id: str,
    finding: str,
    note: str,
    client_time: datetime,
) -> Result:
    roles = {str(r) for r in session.roles}
    if not roles & {"crew", CMO}:
        raise _problem(
            ErrorCode.UNAUTHORIZED_SCOPE, "/", "crew or the crew medical officer record inspections"
        )

    def apply(uow: UnitOfWork, partition: Partition, key: str) -> Result:
        _future(deps, client_time, uow.now, "/client_time")
        docs = deps.documents()
        if lot_id not in {lot.lot_id for lot in lot_views(deps, uow, partition, docs)}:
            raise _problem(ErrorCode.NOT_FOUND, "/lot_id", "no such lot in this environment")
        record_id = deps.ids.new("medi")
        body = {
            "inspection_id": record_id,
            "lot_id": lot_id,
            "finding": finding,
            "note": note,
            "at": instant(uow.now),
            "inspector_role": CMO if CMO in roles else "crew",
        }
        deps.repository.add_record(
            uow,
            RecordRow(
                record_id,
                "inspection",
                partition.mission_id,
                partition.origin_mode,
                None,
                lot_id,
                partition.environment_id,
                uow.now,
                session.actor.id,
                key,
                body,
            ),
        )
        _schedule_after(deps, uow, partition, "inspection")  # an adverse finding already excludes the lot
        return Result(201, record_id, body)

    return _run_command(
        deps, session, cmd, "recordMedicineInspection", lot_id, apply, lambda v, r: _body(deps, v, r)
    )


def _schedule_after(deps: Deps, uow: UnitOfWork, partition: Partition, trigger: str) -> None:
    """Inspections and assays schedule a coalesced run when the hub has a task queue (E32 4.4)."""
    if deps.tasks is not None:
        scheduling.schedule(
            deps.store,
            deps.tasks,
            deps.ids,
            deps.process_epoch,
            uow,
            mission_id=partition.mission_id,
            origin_mode=partition.origin_mode,
            trigger=trigger,
        )


@dataclass(frozen=True, slots=True)
class Assay:
    lot_id: str
    ingredient_id: str
    measured_at: datetime
    measurand: str
    value_percent: Decimal
    sd_percent: Decimal
    site: str
    method: str
    laboratory: str
    note: str


def record_assay(
    deps: Deps, session: SessionContext, cmd: Command, assay: Assay, client_time: datetime
) -> Result:
    """RecordAssay (E32 4.4): the crew medical officer records a measured assay of one ingredient of a lot in this
    environment; it reaches the engine as a pharmacy AssayRecord at the next run, which it schedules."""
    if CMO not in {str(r) for r in session.roles}:
        raise _problem(ErrorCode.UNAUTHORIZED_SCOPE, "/", "the crew medical officer records assays")

    def apply(uow: UnitOfWork, partition: Partition, key: str) -> Result:
        _future(deps, client_time, uow.now, "/client_time")
        _future(deps, assay.measured_at, uow.now, "/measured_at")
        docs = deps.documents()
        lot = next((x for x in lot_views(deps, uow, partition, docs) if x.lot_id == assay.lot_id), None)
        if lot is None:
            raise _problem(ErrorCode.NOT_FOUND, "/lot_id", "no such lot in this environment")
        product = docs.formulary.get(lot.product_id)
        if product is None or assay.ingredient_id not in {i.ingredient_id for i in product.ingredients}:
            raise _problem(
                ErrorCode.INVALID_VALUE, "/ingredient_id", "not an ingredient of this lot's product"
            )
        record_id = deps.ids.new("medy")
        body = {
            "assay_id": record_id,
            "lot_id": assay.lot_id,
            "ingredient_id": assay.ingredient_id,
            "measured_at": instant(assay.measured_at),
            "measurand": assay.measurand,
            "value_percent": assay.value_percent,
            "sd_percent": assay.sd_percent,
            "site": assay.site,
            "method": assay.method,
            "laboratory": assay.laboratory,
            "note": assay.note,
            "at": instant(uow.now),
        }
        deps.repository.add_record(
            uow,
            RecordRow(
                record_id,
                "assay",
                partition.mission_id,
                partition.origin_mode,
                None,
                assay.lot_id,
                partition.environment_id,
                uow.now,
                session.actor.id,
                key,
                body,
            ),
        )
        _schedule_after(deps, uow, partition, "assay")
        return Result(201, record_id, body)

    return _run_command(
        deps, session, cmd, "recordMedicineAssay", assay.lot_id, apply, lambda v, r: _body(deps, v, r)
    )


_JOB_STATE = {
    "queued": "queued",
    "running": "running",
    "completed": "succeeded",
    "failed": "failed",
    "timed_out": "failed",
    "interrupted": "failed",
}


def job_view(deps: Deps, view: View, run_id: str) -> dict[str, object]:
    """The Job of job.schema.json for one assessment run (`job_id` is the run ID)."""
    run = deps.store.run(view, run_id)
    if run is None:
        raise _problem(ErrorCode.NOT_FOUND, "/job_id", "no such assessment")
    status, error_code = deps.store.run_status(view, run_id)
    error = (
        None
        if error_code is None
        else {"code": ErrorCode.INTERNAL_ERROR.value, "message": error_code, "retryable": True}
    )
    return {
        "job_id": run_id,
        "kind": "assessment",
        "state": _JOB_STATE[status],
        "progress": 1 if status in ("completed", "failed", "timed_out", "interrupted") else 0,
        "result_ref": None,
        "error": error,
        "created_at": instant(run.at),
        "updated_at": instant(view.now),
    }


def schedule_assessment(deps: Deps, session: SessionContext, cmd: Command) -> Result:
    """scheduleMedicineAssessment: coalesced per mission and mode; CAPABILITY_UNAVAILABLE without a task queue."""
    if deps.tasks is None:
        raise scheduling.unavailable()

    def apply(uow: UnitOfWork, partition: Partition, key: str) -> Result:
        done = scheduling.schedule(
            deps.store,
            deps.tasks,
            deps.ids,
            deps.process_epoch,
            uow,
            mission_id=partition.mission_id,
            origin_mode=partition.origin_mode,
            trigger="operator",
            command_key=key,
        )
        return Result(202, done.run.run_id, job_view(deps, uow, done.run.run_id))

    return _run_command(
        deps,
        session,
        cmd,
        "scheduleMedicineAssessment",
        "assessment",
        apply,
        lambda v, r: job_view(deps, v, r),
    )


def record_context(
    deps: Deps,
    session: SessionContext,
    cmd: Command,
    subject: str,
    part: str,
    state: str,
    items: Sequence[Mapping[str, object]],
    client_time: datetime,
) -> Result:
    def apply(uow: UnitOfWork, partition: Partition, key: str) -> Result:
        _future(deps, client_time, uow.now, "/client_time")
        _require_read(deps, uow, session, subject, "context")
        _require_clinician(deps, uow, session, subject)
        if part not in PARTS:
            raise _problem(ErrorCode.INVALID_VALUE, "/part", "unknown part")
        record_id = deps.ids.new("medc")
        body = {
            "part": part,
            "state": state,
            "items": list(items),
            "recorded_by": session.person_id or session.actor.id,
        }
        deps.repository.add_record(
            uow,
            RecordRow(
                record_id,
                "context",
                partition.mission_id,
                partition.origin_mode,
                subject,
                None,
                None,
                uow.now,
                session.actor.id,
                key,
                body,
            ),
        )
        return Result(201, record_id, context_view(deps, uow, partition, subject))

    def replay(uow: UnitOfWork, _: str) -> Mapping[str, object]:
        return context_view(deps, uow, _partition(deps, uow), subject)

    return _run_command(deps, session, cmd, "recordMedicationContext", subject, apply, replay)


def context_view(deps: Deps, view: View, partition: Partition, subject: str) -> dict[str, object]:
    latest: dict[str, RecordRow] = {}
    for row in deps.repository.records(
        view, "context", partition, limit=deps.limits.max_rows, subject_person_id=subject
    ):
        latest[str(row.body["part"])] = row
    return {
        "subject_person_id": subject,
        "parts": {
            name: None
            if name not in latest
            else {
                "state": latest[name].body["state"],
                "items": latest[name].body.get("items") or [],
                "recorded_by": latest[name].body["recorded_by"],
                "recorded_at": instant(latest[name].at),
                "statement_id": latest[name].record_id,
            }
            for name in PARTS
        },
        "as_of": instant(view.now),
    }


def get_context(deps: Deps, session: SessionContext, subject: str) -> dict[str, object]:
    with deps.snapshots.snapshot() as view:
        _require_read(deps, view, session, subject, "context")
        return context_view(deps, view, _partition(deps, view), subject)


def record_diagnosis(
    deps: Deps,
    session: SessionContext,
    cmd: Command,
    episode_id: str,
    subject: str,
    condition_id: str,
    basis_text: str,
    certainty_text: str,
    supersedes: str | None,
    client_time: datetime,
) -> Result:
    def apply(uow: UnitOfWork, partition: Partition, key: str) -> Result:
        _future(deps, client_time, uow.now, "/client_time")
        _require_clinician(deps, uow, session, subject)
        docs = deps.documents()
        if condition_id not in docs.condition_ids:
            raise _problem(ErrorCode.INVALID_VALUE, "/condition_id", "condition not in the kit map")
        lineage = None
        if supersedes is not None:
            previous = _diagnosis(deps, uow, partition, supersedes)
            if previous.subject_person_id != subject:
                raise _problem(ErrorCode.NOT_FOUND, "/supersedes", "working diagnosis not found")
            lineage = str(previous.body["lineage_id"])
        record_id = deps.ids.new("medd")
        body = {
            "working_diagnosis_id": record_id,
            "episode_id": episode_id,
            "subject_person_id": subject,
            "condition_id": condition_id,
            "basis_text": basis_text,
            "certainty_text": certainty_text,
            "supersedes": supersedes,
            "lineage_id": lineage or record_id,
            "recorded_by": session.person_id or session.actor.id,
            "at": instant(uow.now),
        }
        deps.repository.add_record(
            uow,
            RecordRow(
                record_id,
                "diagnosis",
                partition.mission_id,
                partition.origin_mode,
                subject,
                None,
                None,
                uow.now,
                session.actor.id,
                key,
                body,
            ),
        )
        return Result(201, record_id, body)

    return _run_command(
        deps, session, cmd, "recordWorkingDiagnosis", episode_id, apply, lambda v, r: _body(deps, v, r)
    )


def treatment_options(deps: Deps, session: SessionContext, diagnosis_id: str) -> dict[str, object]:
    with deps.snapshots.snapshot() as view:
        partition = _partition(deps, view)
        row = _diagnosis(deps, view, partition, diagnosis_id)
        subject = str(row.subject_person_id)
        _require_read(deps, view, session, subject, "context")
        _require_clinician(deps, view, session, subject)
        docs = deps.documents()
        lots = lot_views(deps, view, partition, docs)
        options = []
        for line in docs.treatments.indications.get(str(row.body["condition_id"]), ()):
            rule = docs.treatments.rules.get(line.rule_id)
            product = docs.formulary.get(line.product_id)
            options.append(
                {
                    "line": line.line,
                    "product_id": line.product_id,
                    "name": product.name if product else line.product_id,
                    "rule_id": line.rule_id,
                    "review_state": rule.review_state if rule else None,
                    "usable_lot_ids": [
                        lot.lot_id
                        for lot in lots
                        if lot.product_id == line.product_id and not exclusions(lot, view.now)
                    ],
                }
            )
        return {
            "working_diagnosis_id": diagnosis_id,
            "condition_id": row.body["condition_id"],
            "options": options,
        }


def create_proposal(
    deps: Deps,
    session: SessionContext,
    cmd: Command,
    diagnosis_id: str,
    product_id: str,
    lot_id: str | None,
    labeled_only: bool,
    client_time: datetime,
) -> Result:
    def apply(uow: UnitOfWork, partition: Partition, key: str) -> Result:
        _future(deps, client_time, uow.now, "/client_time")
        diagnosis = _diagnosis(deps, uow, partition, diagnosis_id)
        subject = str(diagnosis.subject_person_id)
        _require_read(deps, uow, session, subject, "proposals")
        _require_clinician(deps, uow, session, subject)
        docs = deps.documents()
        example = activation_findings(docs.treatments, docs.policy, partition.origin_mode)
        if example:
            raise ProblemError(ErrorCode.CONTEXT_INAPPLICABLE, example[0].text, retryable=False)
        assembled = assemble(deps, uow, partition, docs, subject, product_id)
        request = DoseRequest(
            subject,
            WorkingDiagnosis(
                diagnosis.record_id, str(diagnosis.body["condition_id"]), str(diagnosis.body["lineage_id"])
            ),
            product_id,
            lot_id,
            labeled_only,
        )
        proposal = propose(request, assembled.context)
        record_id = deps.ids.new("medp")
        expires = uow.now + timedelta(seconds=docs.policy.proposal_ttl_seconds)
        state = "proposed" if proposal.outcome == "proposal" else "refusal"
        wire = proposal_wire(
            proposal,
            proposal_id=record_id,
            subject_person_id=subject,
            working_diagnosis_id=diagnosis_id,
            created_at=uow.now,
            expires_at=expires,
            origin_mode=partition.origin_mode,
            state=state,
        )
        internal = {
            "fingerprint": assembled.fingerprint,
            "process_epoch": deps.process_epoch,
            "labeled_only": labeled_only,
            "rule_id": proposal.rule_id,
            "lineage_id": str(diagnosis.body["lineage_id"]),
            "environment_id": partition.environment_id,
        }
        _supersede_open(deps, uow, partition, subject, product_id, docs)
        deps.repository.add_record(
            uow,
            RecordRow(
                record_id,
                "proposal",
                partition.mission_id,
                partition.origin_mode,
                subject,
                proposal.lot_id,
                partition.environment_id,
                uow.now,
                session.actor.id,
                key,
                {"wire": wire, "internal": internal},
            ),
        )
        events.proposal_changed(uow, record_id, subject, "proposed" if state == "proposed" else "refused")
        return Result(201, record_id, wire)

    return _run_command(
        deps, session, cmd, "createDoseProposal", diagnosis_id, apply, lambda v, r: proposal_view(deps, v, r)
    )


def _supersede_open(
    deps: Deps, uow: UnitOfWork, partition: Partition, subject: str, product_id: str, docs: Documents
) -> None:
    ingredients = (
        {i.ingredient_id for i in docs.formulary[product_id].ingredients}
        if product_id in docs.formulary
        else set()
    )
    for row in deps.repository.records(
        uow, "proposal", partition, limit=deps.limits.max_rows, subject_person_id=subject
    ):
        wire = cast(Mapping[str, object], row.body["wire"])
        if wire["state"] != "proposed" or deps.repository.terminal(uow, row.record_id) is not None:
            continue
        other = docs.formulary.get(str(wire["product_id"]))
        if other is not None and {i.ingredient_id for i in other.ingredients} & ingredients:
            deps.repository.add_terminal(
                uow, row.record_id, "superseded", "a newer proposal for the same ingredients"
            )
            events.proposal_changed(uow, row.record_id, subject, "superseded")


def proposal_view(deps: Deps, view: View, proposal_id: str) -> dict[str, object]:
    row = deps.repository.record(view, proposal_id)
    if row is None or row.kind != "proposal":
        raise _problem(ErrorCode.NOT_FOUND, "/proposal_id", "proposal not found")
    wire = dict(cast(Mapping[str, object], row.body["wire"]))
    terminal = deps.repository.terminal(view, proposal_id)
    internal = cast(Mapping[str, object], row.body["internal"])
    if terminal is not None:
        wire["state"] = terminal[0]
    elif wire["state"] == "proposed" and (
        internal["process_epoch"] != deps.process_epoch or view.now >= parse_instant(wire["expires_at"])
    ):
        wire["state"] = "expired"  # C5 and the TTL: shown expired; the event is appended by the next write
    return wire


def get_proposal(deps: Deps, session: SessionContext, proposal_id: str) -> dict[str, object]:
    with deps.snapshots.snapshot() as view:
        row = deps.repository.record(view, proposal_id)
        partition = _partition(deps, view)
        if (
            row is None
            or row.kind != "proposal"
            or (row.mission_id, row.origin_mode) != (partition.mission_id, partition.origin_mode)
        ):
            raise _problem(ErrorCode.NOT_FOUND, "/proposal_id", "proposal not found")
        _require_read(deps, view, session, str(row.subject_person_id), "proposals")
        return proposal_view(deps, view, proposal_id)


@dataclass(frozen=True, slots=True)
class Override:
    alert_id: str
    reason_code: str
    note: str


def _open_proposal(
    deps: Deps,
    uow: UnitOfWork,
    partition: Partition,
    session: SessionContext,
    proposal_id: str,
    if_match: str,
    lot_id: str,
) -> RecordRow:
    """ConfirmDose steps 1 and 3: the proposal exists here, the caller may read it (before any error that carries
    it), it is an open proposal (one terminal event; a refusal is never confirmed) at the If-Match revision, and it
    names this lot."""
    row = deps.repository.record(uow, proposal_id)
    if (
        row is None
        or row.kind != "proposal"
        or (row.mission_id, row.origin_mode)
        != (
            partition.mission_id,
            partition.origin_mode,
        )
    ):
        raise _problem(ErrorCode.NOT_FOUND, "/proposal_id", "proposal not found")
    _require_read(deps, uow, session, str(row.subject_person_id), "proposals")
    wire = cast(Mapping[str, object], row.body["wire"])
    if wire["outcome"] != "proposal" or deps.repository.terminal(uow, proposal_id) is not None:
        raise ProblemError(
            ErrorCode.INVALID_TRANSITION,
            "the proposal is not open",
            current_state=proposal_view(deps, uow, proposal_id),
        )
    if if_match.strip('"') != str(wire["revision"]):
        raise ProblemError(ErrorCode.STALE_VERSION, "revision differs", current_state=wire)
    if lot_id != wire["lot_id"]:
        raise _problem(ErrorCode.INVALID_VALUE, "/lot_id", "another lot needs a new proposal")
    return row


def _recompute(
    deps: Deps, uow: UnitOfWork, partition: Partition, row: RecordRow, docs: Documents
) -> tuple[Proposal, Assembled, RecordRow]:
    """ConfirmDose step 4: the whole proposal again, from this transaction's reads."""
    wire = cast(Mapping[str, object], row.body["wire"])
    internal = cast(Mapping[str, object], row.body["internal"])
    subject = str(row.subject_person_id)
    diagnosis = _diagnosis(deps, uow, partition, str(wire["working_diagnosis_id"]))
    assembled = assemble(deps, uow, partition, docs, subject, str(wire["product_id"]))
    request = DoseRequest(
        subject,
        WorkingDiagnosis(
            diagnosis.record_id, str(diagnosis.body["condition_id"]), str(diagnosis.body["lineage_id"])
        ),
        str(wire["product_id"]),
        str(wire["lot_id"]),
        bool(internal["labeled_only"]),
    )
    return propose(request, assembled.context), assembled, diagnosis


def _unchanged(row: RecordRow, again: Proposal, assembled: Assembled) -> bool:
    wire = cast(Mapping[str, object], row.body["wire"])
    internal = cast(Mapping[str, object], row.body["internal"])
    return (
        assembled.fingerprint == internal["fingerprint"]
        and again.outcome == "proposal"
        and again.amount is not None
        and amount_json(again.amount) == _amount_value(wire["amount"])
        and not assembled.context.clock_discontinuity
    )


def _alerts_answered(
    again: Proposal,
    who: Confirmer,
    subject: str,
    overrides: Sequence[Override],
    acknowledged: Sequence[str],
) -> list[dict[str, object]]:
    """ConfirmDose step 5: every override_required alert has an eligible override with a listed reason, and every
    acknowledge alert is acknowledged. Returns the overrides to store."""
    given = {o.alert_id: o for o in overrides}
    done: list[dict[str, object]] = []
    for alert in again.alerts:
        if alert.level == "acknowledge" and alert.alert_id not in acknowledged:
            raise _problem(
                ErrorCode.INVALID_VALUE, "/acknowledged_alert_ids", f"{alert.alert_id} needs acknowledgement"
            )
        if alert.level != "override_required":
            continue
        override = given.get(alert.alert_id)
        if override is None:
            raise _problem(ErrorCode.INVALID_VALUE, "/overrides", f"{alert.alert_id} needs an override")
        if not may_override(who, subject, alert, override.reason_code):
            raise _problem(ErrorCode.ROLE_NOT_ELIGIBLE, "/overrides", f"may not override {alert.alert_id}")
        done.append(
            {
                "alert_id": alert.alert_id,
                "kind": alert.kind,
                "reason_code": override.reason_code,
                "note": override.note,
                "by": who.person_id,
            }
        )
    return done


def confirm_proposal(
    deps: Deps,
    session: SessionContext,
    cmd: Command,
    proposal_id: str,
    if_match: str,
    lot_id: str,
    overrides: Sequence[Override],
    acknowledged: Sequence[str],
    client_time: datetime,
) -> Result:
    """E32 4.4 ConfirmDose, one unit of work. A supersede or expiry is committed and returned as 409 with the proposal;
    an ineligible confirmer writes nothing."""

    def apply(uow: UnitOfWork, partition: Partition, key: str) -> Result:
        _future(deps, client_time, uow.now, "/client_time")
        row = _open_proposal(deps, uow, partition, session, proposal_id, if_match, lot_id)
        wire = cast(Mapping[str, object], row.body["wire"])
        internal = cast(Mapping[str, object], row.body["internal"])
        subject, docs = str(row.subject_person_id), deps.documents()
        product = docs.treatments.products.get(str(wire["product_id"]))
        who = _confirmer(deps, uow, session, subject)
        if product is None or not may_confirm(
            who, subject, product.confirm_by, _plan_names(deps, uow, row, str(wire["product_id"]))
        ):
            raise _problem(ErrorCode.ROLE_NOT_ELIGIBLE, "/", "not eligible to confirm this proposal")
        if internal["process_epoch"] != deps.process_epoch or uow.now >= parse_instant(wire["expires_at"]):
            return _terminal(deps, uow, proposal_id, "expired", "process restarted or time to live passed")
        again, assembled, diagnosis = _recompute(deps, uow, partition, row, docs)
        if not _unchanged(row, again, assembled) or again.amount is None:
            return _terminal(deps, uow, proposal_id, "superseded", "the context changed since the proposal")
        done = _alerts_answered(again, who, subject, overrides, acknowledged)
        lot = next(x for x in assembled.context.lots if x.lot_id == lot_id)
        administered = Administered(
            subject=subject,
            lot=lot,
            amount=again.amount,
            accounting={x.ingredient_id: x.accounting for x in again.ingredients},
            proposal_id=proposal_id,
            outside_engine=False,
            overrides=done,
            findings=(),
            rule_id=again.rule_id,
            lineage_id=str(diagnosis.body["lineage_id"]),
            administered_at=uow.now,
            who=who,
        )
        body = _administer(deps, uow, partition, session, key, administered)
        deps.repository.add_terminal(uow, proposal_id, "confirmed", str(body["administration_id"]))
        events.proposal_changed(uow, proposal_id, subject, "confirmed")
        return Result(201, str(body["administration_id"]), body)

    return _run_command(
        deps,
        session,
        cmd,
        "confirmDoseProposal",
        proposal_id,
        apply,
        lambda v, r: _replay_any(deps, v, r),
        {"if_match": if_match.strip('"')},
    )


def _plan_names(deps: Deps, uow: UnitOfWork, proposal: RecordRow, product_id: str) -> bool:
    """Q14: the subject's active plan step names this medicine for the proposal's condition, from E28 in this
    transaction. Absent provider: no plan grants it."""
    if deps.plans is None:
        return False
    wire = cast(Mapping[str, object], proposal.body["wire"])
    diagnosis = deps.repository.record(uow, str(wire["working_diagnosis_id"]))
    if diagnosis is None:
        return False
    return deps.plans.names_medicine(
        uow,
        subject_person_id=str(proposal.subject_person_id),
        condition_id=str(diagnosis.body["condition_id"]),
        product_id=product_id,
    )


def _amount_value(raw: object) -> object:
    if raw is None:
        return None
    data = cast(Mapping[str, object], raw)
    return {"value": Decimal(str(data["value"])), "ucum": data["ucum"]}


def _replay_any(deps: Deps, view: View, record_id: str) -> Mapping[str, object]:
    row = deps.repository.record(view, record_id)
    if row is not None and row.kind == "proposal":
        return proposal_view(deps, view, record_id)
    return administration_view(deps, view, record_id)


def _terminal(deps: Deps, uow: UnitOfWork, proposal_id: str, state: str, reason: str) -> Result:
    deps.repository.add_terminal(uow, proposal_id, state, reason)
    row = deps.repository.record(uow, proposal_id)
    if row is not None and row.subject_person_id is not None:
        events.proposal_changed(uow, proposal_id, row.subject_person_id, state)
    return Result(409, proposal_id, proposal_view(deps, uow, proposal_id))


@dataclass(frozen=True, slots=True)
class Administered:
    """One administration to record: from a confirmed proposal, or manual (outside the engine)."""

    subject: str
    lot: LotView
    amount: Amount
    accounting: Mapping[str, Amount]  # per ingredient, the same Acc_i every later limit counts
    proposal_id: str | None
    outside_engine: bool
    overrides: Sequence[Mapping[str, object]]
    findings: Sequence[str]
    rule_id: str | None
    lineage_id: str | None
    administered_at: datetime
    who: Confirmer
    reason_text: str | None = None


def _check_draw(lot: LotView, amount: Amount, field: str) -> None:
    """INV-M4 through the supplies stock port: a lot never goes below zero, and units never mix."""
    unit = Unit(amount.ucum)
    level = StockLevel(lot.lot_id, lot.quantity_now > 0, Quantity(lot.quantity_now, unit), unit)
    try:
        drawn(level, Quantity(amount.value, unit))
    except StockBelowZero as error:
        raise ProblemError(
            ErrorCode.SUPPLY_BELOW_ZERO,
            f"{lot.lot_id} holds {lot.quantity_now}",
            field_errors=(FieldError(field, ErrorCode.SUPPLY_BELOW_ZERO.value, "not enough in the lot"),),
        ) from error
    except StockUnitMismatch as error:
        raise _problem(ErrorCode.INVALID_VALUE, field, "the unit differs from the lot's") from error


def _administration_body(
    deps: Deps, partition: Partition, session: SessionContext, administration_id: str, a: Administered
) -> dict[str, object]:
    rule = deps.documents().treatments.rules.get(a.rule_id) if a.rule_id else None
    interval = max((i.min_interval_seconds for i in rule.ingredients), default=0) if rule else 0
    next_dose = instant(a.administered_at + timedelta(seconds=interval)) if interval else None
    return {
        "administration_id": administration_id,
        "revision": 1,
        "proposal_id": a.proposal_id,
        "outside_engine": a.outside_engine,
        "subject_person_id": a.subject,
        "product_id": a.lot.product_id,
        "lot_id": a.lot.lot_id,
        "amount": amount_json(a.amount),
        "ingredients": [
            {"ingredient_id": k, "accounting": amount_json(v)} for k, v in sorted(a.accounting.items())
        ],
        "administered_at": instant(a.administered_at),
        "confirmed_by": a.who.person_id or session.actor.id,
        "confirmed_as": a.who.authority,
        "role_assignment_id": a.who.role_assignment_id,
        "overrides": list(a.overrides),
        "limit_findings": list(a.findings),
        "status": "completed",
        "next_dose_earliest_at": next_dose,
        "origin_mode": partition.origin_mode,
        "rule_id": a.rule_id,
        "lineage_id": a.lineage_id,
        "reason_text": a.reason_text,
    }


def _administer(
    deps: Deps, uow: UnitOfWork, partition: Partition, session: SessionContext, key: str, a: Administered
) -> dict[str, object]:
    """The administration, its accounting rows, the stock movement and the audit entry, in the caller's unit of
    work: all of them or none (crash at any step rolls the whole command back)."""
    _check_draw(a.lot, a.amount, "/amount")
    administration_id = deps.ids.new("meda")
    body = _administration_body(deps, partition, session, administration_id, a)
    deps.repository.add_record(
        uow,
        RecordRow(
            administration_id,
            "administration",
            partition.mission_id,
            partition.origin_mode,
            a.subject,
            a.lot.lot_id,
            partition.environment_id,
            uow.now,
            session.actor.id,
            key,
            body,
        ),
    )
    deps.repository.add_ingredients(
        uow, administration_id, {k: (v.value, v.ucum) for k, v in a.accounting.items()}
    )
    movement = Movement(
        deps.ids.new("medm"),
        partition,
        a.lot.lot_id,
        "administration",
        -a.amount.value,
        a.amount.ucum,
        administration_id,
        session.actor.id,
        None,
    )
    deps.repository.add_movement(uow, movement)
    _audit(deps, uow, AUDIT_ADMINISTERED, a.who)
    events.administered(uow, administration_id, a.subject, a.proposal_id, a.outside_engine)
    return administration_view(deps, uow, administration_id)


def audit_policies() -> tuple[AuditPolicy, ...]:
    """SYSTEM 5.8: the audit entries `_audit` writes. The platform audit log refuses an entry whose type has no
    registered policy (INVALID_VALUE), so module.register declares each one through registry.audit_policy."""
    return (
        AuditPolicy(AUDIT_ADMINISTERED, {"confirmed_as": CONFIRMED_AS}, frozenset(), {}),
        AuditPolicy(
            AUDIT_ADMINISTERED_ACTING,
            {"confirmed_as": CONFIRMED_AS},
            frozenset({"role_assignment"}),
            {"role_assignment": frozenset({"role_assignment"})},
        ),
        AuditPolicy(AUDIT_CORRECTED, {}, frozenset(), {}),
    )


def _audit(deps: Deps, uow: UnitOfWork, event_type: str, who: Confirmer | None = None) -> None:
    """No private text in the audit trail: the event type, the actor and, for an administration, the authority it
    was confirmed under; an acting officer's entry names the accepted assignment (SYSTEM 8.4 rule 3; AGENTS 6)."""
    acting = who is not None and who.role_assignment_id is not None
    uow.audit(
        AuditEntry(
            event_id=deps.ids.new("aud"),
            event_type=AUDIT_ADMINISTERED_ACTING if acting else event_type,
            event_version=1,
            actor=uow.actor,
            refs={"role_assignment": str(who.role_assignment_id)} if acting and who is not None else {},
            correlation_id=uow.correlation_id,
            causation_id=None,
            safe_metadata={} if who is None else {"confirmed_as": who.authority},
        )
    )


def administration_view(deps: Deps, view: View, administration_id: str) -> dict[str, object]:
    row = deps.repository.record(view, administration_id)
    if row is None or row.kind != "administration":
        raise _problem(ErrorCode.NOT_FOUND, "/administration_id", "administration not found")
    body = {k: v for k, v in row.body.items() if k not in _INTERNAL}
    if deps.repository.correction(view, administration_id) is not None:
        body["status"] = "entered_in_error"
        body["revision"] = 2
    return body


def record_manual(
    deps: Deps,
    session: SessionContext,
    cmd: Command,
    subject: str,
    product_id: str,
    lot_id: str,
    amount: Amount,
    administered_at: datetime,
    reason_text: str,
) -> Result:
    """Brief decision 9: a fact a person already did, bounded by stock and flagged; it never bypasses a limit
    silently (limit_findings) and counts toward every later limit with its accounting amount."""

    def apply(uow: UnitOfWork, partition: Partition, key: str) -> Result:
        _future(deps, administered_at, uow.now, "/administered_at")
        _require_read(deps, uow, session, subject, "administrations")
        who = _require_clinician(deps, uow, session, subject)
        docs = deps.documents()
        formulary = docs.formulary.get(product_id)
        if formulary is None:
            raise _problem(ErrorCode.INVALID_VALUE, "/product_id", "not in the formulary")
        if amount.ucum != formulary.per or amount.value <= 0:
            raise _problem(ErrorCode.INVALID_VALUE, "/amount", f"an amount above zero in {formulary.per}")
        assembled = assemble(deps, uow, partition, docs, subject, product_id)
        lot = next(
            (x for x in assembled.context.lots if x.lot_id == lot_id and x.product_id == product_id), None
        )
        if lot is None:
            raise _problem(ErrorCode.NOT_FOUND, "/lot_id", "no such lot of this product in this environment")
        request = DoseRequest(subject, None, product_id, lot_id, True)
        accounting, findings = manual_findings(assembled.context, request, lot, amount)
        administered = Administered(
            subject=subject,
            lot=lot,
            amount=amount,
            accounting=accounting,
            proposal_id=None,
            outside_engine=True,
            overrides=(),
            findings=findings,
            rule_id=None,
            lineage_id=None,
            administered_at=administered_at,
            who=who,
            reason_text=reason_text,
        )
        body = _administer(deps, uow, partition, session, key, administered)
        return Result(201, str(body["administration_id"]), body)

    return _run_command(
        deps,
        session,
        cmd,
        "recordManualAdministration",
        subject,
        apply,
        lambda v, r: administration_view(deps, v, r),
    )


def list_administrations(deps: Deps, session: SessionContext, subject: str) -> dict[str, object]:
    if session.kind == "station":
        raise _problem(ErrorCode.UNAUTHORIZED_SCOPE, "/", "never to a station session")
    with deps.snapshots.snapshot() as view:
        _require_read(deps, view, session, subject, "administrations")
        partition = _partition(deps, view)
        rows = deps.repository.records(
            view, "administration", partition, limit=deps.limits.max_rows, subject_person_id=subject
        )
        return {
            "subject_person_id": subject,
            "items": [administration_view(deps, view, r.record_id) for r in rows],
        }


def correct_administration(
    deps: Deps, session: SessionContext, cmd: Command, administration_id: str, reason_text: str
) -> Result:
    def apply(uow: UnitOfWork, partition: Partition, key: str) -> Result:
        row = deps.repository.record(uow, administration_id)
        if (
            row is None
            or row.kind != "administration"
            or (row.mission_id, row.origin_mode)
            != (
                partition.mission_id,
                partition.origin_mode,
            )
        ):
            raise _problem(ErrorCode.NOT_FOUND, "/administration_id", "administration not found")
        _require_read(deps, uow, session, str(row.subject_person_id), "administrations")
        _require_clinician(deps, uow, session, str(row.subject_person_id))
        if deps.repository.correction(uow, administration_id) is not None:
            raise ProblemError(
                ErrorCode.INVALID_TRANSITION,
                "already corrected",
                current_state=administration_view(deps, uow, administration_id),
            )
        deps.repository.add_correction(uow, administration_id, reason_text, session.actor.id, key)
        _audit(deps, uow, AUDIT_CORRECTED)
        events.corrected(uow, administration_id, str(row.subject_person_id))
        return Result(201, administration_id, administration_view(deps, uow, administration_id))

    return _run_command(
        deps,
        session,
        cmd,
        "correctMedicationAdministration",
        administration_id,
        apply,
        lambda v, r: administration_view(deps, v, r),
    )


def record_movement(
    deps: Deps,
    session: SessionContext,
    cmd: Command,
    lot_id: str,
    kind: str,
    quantity: Amount,
    client_time: datetime,
) -> Result:
    def apply(uow: UnitOfWork, partition: Partition, key: str) -> Result:
        _future(deps, client_time, uow.now, "/client_time")
        if not may_record_manual(_confirmer(deps, uow, session, ""), ""):
            raise _problem(ErrorCode.ROLE_NOT_ELIGIBLE, "/", "the crew medical officer or the acting officer")
        docs = deps.documents()
        lot = next((x for x in lot_views(deps, uow, partition, docs) if x.lot_id == lot_id), None)
        if lot is None:
            raise _problem(ErrorCode.NOT_FOUND, "/lot_id", "no such lot in this environment")
        unit = docs.formulary[lot.product_id].per
        if quantity.ucum != unit:
            raise _problem(ErrorCode.INVALID_VALUE, "/quantity", f"the lot counts in {unit}")
        if kind == "return":
            delta = quantity.value
        elif kind == "waste":
            delta = -quantity.value
        else:
            delta = quantity.value - lot.quantity_now
        if delta < 0:
            _check_draw(lot, Amount(-delta, unit), "/quantity")
        movement_id = deps.ids.new("medm")
        deps.repository.add_movement(
            uow, Movement(movement_id, partition, lot_id, kind, delta, unit, None, session.actor.id, key)
        )
        return Result(
            201,
            movement_id,
            {
                "movement_id": movement_id,
                "lot_id": lot_id,
                "kind": kind,
                "quantity_now": {"value": lot.quantity_now + delta, "ucum": unit},
            },
        )

    def replay(uow: UnitOfWork, movement_id: str) -> Mapping[str, object]:
        return {"movement_id": movement_id, "lot_id": lot_id, "kind": kind}

    return _run_command(deps, session, cmd, "recordLotMovement", lot_id, apply, replay)


def lots_view(deps: Deps, session: SessionContext, environment_id: str | None) -> dict[str, object]:
    roles = {str(r) for r in session.roles}
    if not roles & {"crew", CMO, "operator"}:
        raise _problem(ErrorCode.UNAUTHORIZED_SCOPE, "/", "not allowed")
    with deps.snapshots.snapshot() as view:
        partition = _partition(deps, view)
        if environment_id is not None:
            partition = replace(partition, environment_id=environment_id)
        docs = deps.documents()
        run = deps.store.latest_completed(view, partition.mission_id, partition.origin_mode)
        lots = []
        for lot in lot_views(deps, view, partition, docs):
            product = docs.formulary.get(lot.product_id)
            summary = (
                fresh(lot.summary, _board_context(view.now, partition.origin_mode, docs), product)
                if product
                else None
            )
            lots.append(
                {
                    "lot_id": lot.lot_id,
                    "product_id": lot.product_id,
                    "name": product.name if product else lot.product_id,
                    "quantity_now": {"value": lot.quantity_now, "ucum": product.per if product else "{unit}"},
                    "lot_status": lot.lot_status,
                    "excluded_by": list(exclusions(lot, view.now)),
                    "state": None if lot.summary is None else lot.summary.state,
                    "potency": []
                    if summary is None
                    else [
                        {"ingredient_id": k, "low": v.low, "median": v.median, "high": v.high}
                        for k, v in sorted(summary.potency.items())
                    ],
                    "crossing_utc": None,
                    "labeled_expiry": instant(lot.labeled_expiry),
                }
            )
        return dict(
            {
                "environment_id": partition.environment_id,
                "assessment": None
                if run is None or run.start is None
                else {
                    "run_id": run.run_id,
                    "as_of": instant(run.start.as_of),
                    "status": _assessment_status(deps, view, partition),
                    "origin_mode": run.origin_mode,
                },
                "lots": lots,
            }
        )


def _assessment_status(deps: Deps, view: View, partition: Partition) -> str:
    """`completed` while the newest finished run is the one in use; `unknown` when a newer run ended without a
    valid result (failed, timed out, interrupted, result_invalid): the last completed lots and their exclusions stay
    in use, and the board says the current state is not known."""
    for run in deps.store.runs(view, partition.mission_id, partition.origin_mode, limit=deps.limits.max_rows):
        status = deps.store.run_status(view, run.run_id)[0]
        if status not in {"queued", "running"}:
            return "completed" if status == "completed" else "unknown"
    return "unknown"


def _board_context(now: datetime, mode: str, docs: Documents) -> DoseContext:
    """The fields `fresh` reads (now, the policy's age bound), for the lots board."""
    return DoseContext(
        now, cast(Mode, mode), docs.treatments, docs.policy, docs.formulary, (), (), MedicationContext()
    )


def active_medicine_names(deps: Deps, session: SessionContext, subject: str) -> tuple[str, ...]:
    """MedicationRecordReader (E32 4.3), names only. Active: an other medicine on the latest statement whose `until`
    is absent or later than now, or a product administered (not entered in error) inside its rule's longest limit
    window, so still counting against a limit. Scope as getMedicationContext; refused, never an empty answer."""
    with deps.snapshots.snapshot() as view:
        _require_read(deps, view, session, subject, "names")
        partition = _partition(deps, view)
        docs = deps.documents()
        context, _ = _context(deps, view, partition, subject)
        names: set[str] = set()
        if context.other_medicines is not None:
            for item in context.other_medicines.items:
                until = item.get("until")
                if isinstance(item.get("name"), str) and (
                    not isinstance(until, datetime) or until > view.now
                ):
                    names.add(str(item["name"]))
        for past in _history(deps, view, partition, subject):
            rule = docs.treatments.rules.get(past.rule_id or "")
            window = (
                max((p.period_seconds for i in rule.ingredients for p in i.max_per_period), default=0)
                if rule is not None
                else 0
            )
            product = _administered_product(deps, view, past.administration_id)
            if product is not None and past.at > view.now - timedelta(seconds=window):
                formulary = docs.formulary.get(product)
                names.add(formulary.name if formulary is not None else product)
        return tuple(sorted(names))


def _administered_product(deps: Deps, view: View, administration_id: str) -> str | None:
    row = deps.repository.record(view, administration_id)
    return None if row is None else cast(str | None, row.body.get("product_id"))
