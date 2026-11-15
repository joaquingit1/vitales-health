"""GetSupplyCover and RecordSupplyUse (E32 4.1): one read snapshot, or one BEGIN IMMEDIATE unit of work."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.commands import AcceptedReceipt, CommandIdentity, StoredResult, command_identity
from crew_health.kernel.errors import FieldError, ProblemError
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import SessionContext, UnitOfWork
from crew_health.supplies.application.context import (
    Deps,
    cover_rows,
    current_mission,
    partition,
    read_heads,
)
from crew_health.supplies.application.events import (
    cover_fact,
    partition_of,
    publish_refusal,
    publish_use,
    use_body,
)
from crew_health.supplies.application.ports import SupplyCoverChanged
from crew_health.supplies.application.references import Reference, check_reference, read_reference
from crew_health.supplies.application.repository import StoredRefusal, StoredUse
from crew_health.supplies.application.wire import Json, encode, instant, supply_entry
from crew_health.supplies.domain.errors import (
    FieldIssue,
    ReleaseMismatch,
    SupplyBelowZero,
    SupplyNotFound,
    UseInvalid,
)
from crew_health.supplies.domain.ledger import admit
from crew_health.supplies.domain.model import Partition, SupplyDef
from crew_health.supplies.domain.quantity import within_bounds

OPERATION = "recordSupplyUse"


@dataclass(frozen=True, slots=True)
class UseRequest:
    supply_id: str
    quantity: Decimal
    plan_run_id: str | None
    help_session_id: str | None
    step_id: str | None
    client_time: datetime
    key: str
    config_release: str | None
    body_sha256: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class UseResult:
    status: int
    supply_use_id: str
    body: bytes


def cover_view(deps: Deps, environment_id: str | None) -> bytes:
    with deps.snapshots.snapshot() as view:
        facts = current_mission(deps, view)
        env = environment_id or facts.active_environment_id
        where = partition(facts, env)
        index = deps.indexes.get(view.release)
        rows = cover_rows(deps, view, index, where, read_heads(deps, view, where))
        body: dict[str, Json] = {
            "environment_id": env,
            "supplies": [supply_entry(row, env, index.words) for row in rows],
            "as_of": instant(view.now),
        }
        return encode(body)


def _command_key(identity: CommandIdentity) -> str:
    return f"api:{identity.actor_ref}:{identity.key}"


def _identity(
    session: SessionContext, request: UseRequest, mission_id: str, origin_mode: str
) -> CommandIdentity:
    """The key covers the operation, the supply (path), the mission and mode, and the body (AGENTS 6)."""
    parameters = {"supply_id": request.supply_id, "mission_id": mission_id, "origin_mode": origin_mode}
    return command_identity(
        key=request.key,
        actor_ref=session.actor.id,
        operation=OPERATION,
        target=request.supply_id,
        parameters_json=json.dumps(parameters, sort_keys=True).encode(),
        body_fingerprint=request.body_sha256,
        fingerprint_kind="sha256",
    )


def validate(deps: Deps, request: UseRequest) -> None:
    issues: list[FieldIssue] = []
    if not request.quantity.is_finite() or request.quantity <= 0:
        issues.append(FieldIssue("/quantity", "INVALID_VALUE", "quantity is above zero"))
    elif not within_bounds(
        request.quantity, maximum=deps.limits.max_quantity, max_scale=deps.limits.max_quantity_scale
    ):
        issues.append(
            FieldIssue("/quantity", "INVALID_VALUE", "quantity outside supplies.max_quantity or scale")
        )
    if request.plan_run_id is not None and request.help_session_id is not None:
        issues.append(
            FieldIssue("/help_session_id", "INVALID_VALUE", "a use names one plan run or one session")
        )
    if request.client_time.tzinfo is None:
        issues.append(FieldIssue("/client_time", "INVALID_VALUE", "client_time carries an offset"))
    if issues:
        raise UseInvalid(issues)


def record_use(deps: Deps, session: SessionContext, request: UseRequest) -> UseResult:
    validate(deps, request)
    with deps.snapshots.snapshot() as view:
        facts = current_mission(deps, view)
    identity = _identity(session, request, facts.mission_id, facts.mission_mode)
    reference = read_reference(deps, session, request.plan_run_id, request.help_session_id)
    try:
        with deps.uow.begin(
            actor=session.actor, command=identity, correlation_id=request.correlation_id
        ) as uow:
            stored = deps.idempotency.lookup(uow, identity)
            if stored is not None:
                return _replay(deps, uow, identity, stored)
            return _apply(deps, uow, identity, request, reference, facts.mission_id)
    except SupplyBelowZero as refused:
        if request.help_session_id is not None:
            _refusal_evidence(deps, session, identity, request, refused)
        raise


def _replay(deps: Deps, uow: UnitOfWork, identity: CommandIdentity, stored: StoredResult) -> UseResult:
    if stored.status == 201:
        use = deps.repository.by_command_key(uow, _command_key(identity))
        if use is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "stored use missing", retryable=False)
        return UseResult(201, use.supply_use_id, encode(use_body(use)))
    code = ErrorCode(stored.result_code)
    raise ProblemError(
        code, code.value, field_errors=(FieldError("/quantity", code.value, "refused before"),)
    )


def _supply(deps: Deps, uow: UnitOfWork, request: UseRequest) -> SupplyDef:
    index = deps.indexes.get(uow.release)
    supply = index.supplies.get(request.supply_id)
    if supply is None:
        raise SupplyNotFound(request.supply_id)
    if not supply.consumable:
        raise UseInvalid((FieldIssue("/quantity", "INVALID_VALUE", "the supply is not used up"),))
    return supply


def _apply(
    deps: Deps,
    uow: UnitOfWork,
    identity: CommandIdentity,
    request: UseRequest,
    reference: Reference,
    mission_id: str,
) -> UseResult:
    if (
        request.config_release is not None
        and request.help_session_id is None
        and request.config_release != uow.release.release_id
    ):
        raise ReleaseMismatch(uow.release.release_id)
    facts = current_mission(deps, uow)
    if (
        facts.mission_id != mission_id
    ):  # the mission changed between the key and the write: retry under the new
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "mission changed", retryable=True)
    supply = _supply(deps, uow, request)
    check_reference(reference, facts, request.supply_id, request.step_id)
    where = partition(facts, facts.active_environment_id)
    use = _admit(deps, uow, identity, request, supply, where)
    deps.repository.append(uow, use)
    deps.repository.register_unit(uow, supply.supply_id, supply.unit.code, first_use_id=use.supply_use_id)
    body = use_body(use)
    publish_use(uow, deps.ids, use, body)
    publish_cover(deps, uow, where, use.supply_use_id, use.observed_at)
    receipt = AcceptedReceipt(
        identity.key, identity.operation, identity.target, deps.ids.new("receipt"), uow.now
    )
    deps.idempotency.record(uow, identity, StoredResult(receipt, 201, use.supply_use_id, "accepted", None))
    return UseResult(201, use.supply_use_id, encode(body))


def observed(client_time: datetime, now: datetime) -> datetime:
    """When the use happened (D3): the client's time when it is not after the hub clock, else the hub clock."""
    return client_time if client_time <= now else now


def _admit(
    deps: Deps,
    uow: UnitOfWork,
    identity: CommandIdentity,
    request: UseRequest,
    supply: SupplyDef,
    where: Partition,
) -> StoredUse:
    if deps.corrupt.has(where, supply.supply_id):
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "supply ledger failed verification", retryable=False)
    index = deps.indexes.get(uow.release)
    row = index.kit.stock.get(where.environment_id, {}).get(supply.supply_id)
    head = deps.repository.head(uow, where, supply.supply_id)
    admitted = admit(supply, row, head, Quantity(request.quantity, supply.unit))
    return StoredUse(
        supply_use_id=deps.ids.new("sup"),
        partition=where,
        supply_id=supply.supply_id,
        quantity=admitted.quantity,
        seq=admitted.seq,
        used_after=admitted.used_after,
        plan_run_id=request.plan_run_id,
        help_session_id=request.help_session_id,
        step_id=request.step_id,
        occurrence=None,
        at=uow.now,
        observed_at=observed(request.client_time, uow.now),
        by_kind=uow.actor.kind,
        by_id=uow.actor.id,
        release_id=uow.release.release_id,
        command_key=_command_key(identity),
        request_sha256=identity.body_fingerprint,
    )


def publish_cover(deps: Deps, uow: UnitOfWork, where: Partition, cause_ref: str, at: datetime) -> None:
    """The in-transaction SupplyCoverChanged of a use for E24 (SYSTEM 8.1): every supply row of the partition,
    sorted by supply ID, in batches of at most supplies.cover_batch_rows rows."""
    index = deps.indexes.get(uow.release)
    rows = cover_rows(deps, uow, index, where, read_heads(deps, uow, where))
    facts = sorted((cover_fact(row, index) for row in rows), key=lambda fact: fact.supply_id)
    size = deps.limits.cover_batch_rows
    for start in range(0, len(facts), size):
        uow.publish(
            SupplyCoverChanged(
                partition=partition_of(where),
                cause="use",
                cause_ref=cause_ref,
                observed_at=at,
                rows=tuple(facts[start : start + size]),
            )
        )


def _refusal_evidence(
    deps: Deps,
    session: SessionContext,
    identity: CommandIdentity,
    request: UseRequest,
    refused: SupplyBelowZero,
) -> None:
    """HZ14: a refused use that names a help session leaves one evidence row and one audit entry, in a second
    unit of work, and the same key replays the refusal (the ledger records 422 SUPPLY_BELOW_ZERO)."""
    del refused
    with deps.uow.begin(actor=session.actor, command=identity, correlation_id=request.correlation_id) as uow:
        if deps.idempotency.lookup(uow, identity) is not None:
            return
        facts = current_mission(deps, uow)
        index = deps.indexes.get(uow.release)
        supply = index.supplies.get(request.supply_id)
        if supply is None or request.help_session_id is None:
            return
        refusal = StoredRefusal(
            refusal_id=deps.ids.new("supr"),
            partition=partition(facts, facts.active_environment_id),
            supply_id=supply.supply_id,
            quantity=Quantity(request.quantity, supply.unit),
            help_session_id=request.help_session_id,
            step_id=request.step_id,
            code=ErrorCode.SUPPLY_BELOW_ZERO.value,
            command_key=_command_key(identity),
            at=uow.now,
            observed_at=observed(request.client_time, uow.now),
        )
        if deps.repository.append_refusal(uow, refusal):
            publish_refusal(uow, deps.ids, refusal, uow.release.release_id)
        receipt = AcceptedReceipt(
            identity.key, identity.operation, identity.target, deps.ids.new("receipt"), uow.now
        )
        result = StoredResult(receipt, 422, refusal.refusal_id, ErrorCode.SUPPLY_BELOW_ZERO.value, None)
        deps.idempotency.record(uow, identity, result)
