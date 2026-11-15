"""Medicines use cases (E32 4.4). The engine proposes; the crew medical officer, or the acting medical officer for an
`any_trained_crew` product at the label dose, confirms. Every safety limit is rechecked inside the confirming
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
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    IdempotencyStore,
    StoredResult,
    command_identity,
)
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext, UnitOfWork, UnitOfWorkFactory
from crew_health.medicines.application.codec import (
    amount_json,
    context_part,
    instant,
    parse_instant,
    proposal_wire,
    summary_from,
    summary_json,
)
from crew_health.medicines.application.ports import (
    ActingOfficerPort,
    MedicinesRepository,
    Movement,
    Partition,
    RecordRow,
    View,
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
from crew_health.medicines.domain.model import (
    Path as PathKind,
)
from crew_health.medicines.domain.units import Amount
from crew_health.supplies.application.ports import (
    MissionStatePort,
    StockBelowZero,
    StockLevel,
    StockUnitMismatch,
    drawn,
)

CMO = "crew_medical_officer"
PARTS = ("allergies", "other_medicines", "renal", "hepatic", "conditions")
AUDIT_ADMINISTERED = "medicines.administered"
AUDIT_CORRECTED = "medicines.corrected"
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


@dataclass(frozen=True, slots=True)
class Deps:
    uow: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    ids: IdFactory
    idempotency: IdempotencyStore
    repository: MedicinesRepository
    mission: MissionStatePort
    acting: ActingOfficerPort | None
    documents: Callable[[], Documents]
    limits: Limits
    process_epoch: str


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
    acting = (
        deps.acting is not None
        and session.person_id is not None
        and deps.acting.is_acting(view, session.person_id, subject)
    )
    return Confirmer(session.person_id, frozenset(str(r) for r in session.roles), acting)


def _require_clinician(deps: Deps, view: View, session: SessionContext, subject: str) -> Confirmer:
    who = _confirmer(deps, view, session, subject)
    if not may_record_manual(who, subject):
        raise _problem(
            ErrorCode.ROLE_NOT_ELIGIBLE,
            "/",
            "the crew medical officer or the acting officer, not the subject",
        )
    return who


def _can_read(deps: Deps, view: View, session: SessionContext, subject: str) -> bool:
    if session.person_id is not None and session.person_id == subject:
        return True
    return CMO in {str(r) for r in session.roles} or (
        deps.acting is not None
        and session.person_id is not None
        and deps.acting.is_acting(view, session.person_id, subject)
    )


def _future(deps: Deps, at: datetime, now: datetime, field: str) -> None:
    if at.tzinfo is None or at > now + timedelta(seconds=deps.limits.future_tolerance_seconds):
        raise _problem(ErrorCode.INVALID_VALUE, field, "a time later than the hub clock is refused (C6)")


def _identity(
    session: SessionContext, cmd: Command, operation: str, target: str, partition: Partition
) -> CommandIdentity:
    parameters = {"target": target, "mission_id": partition.mission_id, "origin_mode": partition.origin_mode}
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
) -> Result:
    """Identity first, then the ledger, then the business checks, in one BEGIN IMMEDIATE unit of work."""
    with deps.snapshots.snapshot() as view:
        partition = _partition(deps, view)
    identity = _identity(session, cmd, operation, target, partition)
    with deps.uow.begin(actor=session.actor, command=identity, correlation_id=cmd.correlation_id) as uow:
        stored = deps.idempotency.lookup(uow, identity)
        if stored is not None:
            if stored.resource_ref is None:
                raise ProblemError(ErrorCode(stored.result_code), "refused before", retryable=False)
            return Result(stored.status, stored.resource_ref, replay(uow, stored.resource_ref))
        now_partition = _partition(deps, uow)
        if now_partition != partition:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "the mission or mode changed; retry", retryable=True)
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
    runs = deps.repository.records(
        view, "assessment", replace(partition, environment_id=""), limit=deps.limits.max_rows
    )
    for row in reversed(runs):
        if row.body.get("status") == "completed":
            lots = cast(Mapping[str, Mapping[str, object]], row.body["lots"])
            return row.record_id, {k: summary_from(v) for k, v in lots.items()}
    return None, {}


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
    ctx = DoseContext(
        now=view.now,
        mode=cast(Mode, partition.origin_mode),
        treatments=docs.treatments,
        policy=docs.policy,
        formulary=docs.formulary,
        lots=lots,
        history=history,
        context=context,
        weights=(),
        clock_discontinuity=_discontinuity(deps, view.now, [h.at for h in history]),
        max_window_rows=deps.limits.max_window_rows,
    )
    material = {
        "mode": partition.origin_mode,
        "treatments": docs.treatments.sha256,
        "policy": docs.policy.sha256,
        "live": docs.policy.live_authorizations.get(product_id),
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
        return Result(201, record_id, body)

    return _run_command(
        deps, session, cmd, "recordMedicineInspection", lot_id, apply, lambda v, r: _body(deps, v, r)
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
        if not _can_read(deps, view, session, subject):
            raise _problem(ErrorCode.NOT_FOUND, "/person_id", "not found")
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
        if not may_record_manual(_confirmer(deps, view, session, subject), subject):
            raise _problem(ErrorCode.NOT_FOUND, "/working_diagnosis_id", "working diagnosis not found")
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
            or not _can_read(deps, view, session, str(row.subject_person_id))
        ):
            raise _problem(ErrorCode.NOT_FOUND, "/proposal_id", "proposal not found")
        return proposal_view(deps, view, proposal_id)


@dataclass(frozen=True, slots=True)
class Override:
    alert_id: str
    reason_code: str
    note: str


def _open_proposal(
    deps: Deps, uow: UnitOfWork, partition: Partition, proposal_id: str, if_match: str, lot_id: str
) -> RecordRow:
    """ConfirmDose steps 1 and 3: the proposal exists here, is an open proposal (one terminal event; a refusal is
    never confirmed) at the If-Match revision, and names this lot."""
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
        row = _open_proposal(deps, uow, partition, proposal_id, if_match, lot_id)
        wire = cast(Mapping[str, object], row.body["wire"])
        internal = cast(Mapping[str, object], row.body["internal"])
        subject, docs = str(row.subject_person_id), deps.documents()
        product = docs.treatments.products.get(str(wire["product_id"]))
        who = _confirmer(deps, uow, session, subject)
        path = cast(PathKind, cast(Mapping[str, object], wire["potency"])["path"])
        if product is None or not may_confirm(who, subject, product.confirm_by, path):
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
        )
        body = _administer(deps, uow, partition, session, key, administered)
        deps.repository.add_terminal(uow, proposal_id, "confirmed", str(body["administration_id"]))
        return Result(201, str(body["administration_id"]), body)

    return _run_command(
        deps, session, cmd, "confirmDoseProposal", proposal_id, apply, lambda v, r: _replay_any(deps, v, r)
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
        "confirmed_by": session.person_id or session.actor.id,
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
    _audit(deps, uow, AUDIT_ADMINISTERED)
    return administration_view(deps, uow, administration_id)


def _audit(deps: Deps, uow: UnitOfWork, event_type: str) -> None:
    """No private text in the audit trail: the event type and the actor only (AGENTS 6)."""
    uow.audit(
        AuditEntry(
            event_id=deps.ids.new("aud"),
            event_type=event_type,
            event_version=1,
            actor=uow.actor,
            refs={},
            correlation_id=uow.correlation_id,
            causation_id=None,
            safe_metadata={},
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
        _require_clinician(deps, uow, session, subject)
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
        if not _can_read(deps, view, session, subject):
            return {"subject_person_id": subject, "items": []}
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
        _require_clinician(deps, uow, session, str(row.subject_person_id))
        if deps.repository.correction(uow, administration_id) is not None:
            raise ProblemError(
                ErrorCode.INVALID_TRANSITION,
                "already corrected",
                current_state=administration_view(deps, uow, administration_id),
            )
        deps.repository.add_correction(uow, administration_id, reason_text, session.actor.id, key)
        _audit(deps, uow, AUDIT_CORRECTED)
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
        run_id, _ = latest_summaries(deps, view, partition)
        run = deps.repository.record(view, run_id) if run_id else None
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
                if run is None
                else {
                    "run_id": run.record_id,
                    "as_of": run.body["as_of"],
                    "status": run.body["status"],
                    "origin_mode": run.origin_mode,
                },
                "lots": lots,
            }
        )


def _board_context(now: datetime, mode: str, docs: Documents) -> DoseContext:
    """The fields `fresh` reads (now, the policy's age bound), for the lots board."""
    return DoseContext(
        now, cast(Mode, mode), docs.treatments, docs.policy, docs.formulary, (), (), MedicationContext()
    )


def store_assessment(
    deps: Deps,
    session: SessionContext,
    status: str,
    as_of: datetime,
    summaries: Mapping[str, LotSummary],
    detail: str,
) -> str:
    """RunAssessment's write: one append-only record per run, `completed` with its lots or `failed`/`timed_out`."""
    with deps.uow.begin(actor=session.actor, correlation_id=deps.ids.new("corr")) as uow:
        partition = _partition(deps, uow)
        record_id = deps.ids.new("medr")
        body = {
            "status": status,
            "as_of": instant(as_of),
            "detail": detail,
            "lots": {k: summary_json(v) for k, v in sorted(summaries.items())},
        }
        deps.repository.add_record(
            uow,
            RecordRow(
                record_id,
                "assessment",
                partition.mission_id,
                partition.origin_mode,
                None,
                None,
                "",
                uow.now,
                session.actor.id,
                None,
                body,
            ),
        )
        return record_id


def active_medicine_names(deps: Deps, session: SessionContext, subject: str) -> tuple[str, ...]:
    """MedicationRecordReader (E32 4.3), names only. Active: an other medicine on the latest statement whose `until`
    is absent or later than now, or a product administered (not entered in error) inside its rule's longest limit
    window, so still counting against a limit. Scope as getMedicationContext; refused, never an empty answer."""
    with deps.snapshots.snapshot() as view:
        if not _can_read(deps, view, session, subject):
            raise _problem(
                ErrorCode.UNAUTHORIZED_SCOPE, "/person_id", "no grant for this crew member's medicines"
            )
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
