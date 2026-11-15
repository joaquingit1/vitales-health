"""RecordStepUses (E32 4.1 and 4.2; R28-20): inside E28's unit of work, one use per consumable pinned need with a
per-use amount, all or nothing, re-entrant by the occurrence key `step:{plan_run_id}:{step_id}:{occurrence}:{supply}`."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.supplies.application.context import Deps, current_mission, partition
from crew_health.supplies.application.events import publish_use, use_body
from crew_health.supplies.application.ports import StepUseConflict, SupplyNeed
from crew_health.supplies.application.release_index import ReleaseIndex
from crew_health.supplies.application.repository import StoredUse
from crew_health.supplies.application.use_cases import publish_cover
from crew_health.supplies.domain.errors import FieldIssue, UnitMismatch, UseInvalid
from crew_health.supplies.domain.ledger import StepUse, admit_step
from crew_health.supplies.domain.model import Admitted, Head, Partition, SupplyDef
from crew_health.supplies.domain.quantity import add, boundary_unit, decimal_text


def occurrence_key(plan_run_id: str, step_id: str, occurrence: int, supply_id: str) -> str:
    return f"step:{plan_run_id}:{step_id}:{occurrence}:{supply_id}"


def body_sha256(where: Partition, supply_id: str, amount: Quantity) -> str:
    """The identity of one step use beyond its occurrence key: partition, supply, exact amount and unit. A replay
    under the same key with any other value is a conflict (AGENTS 6: the key covers the body)."""
    body = {
        "mission_id": where.mission_id,
        "origin_mode": where.origin_mode,
        "environment_id": where.environment_id,
        "custody_location_id": where.custody_location_id,
        "supply_id": supply_id,
        "quantity": decimal_text(amount.value),
        "unit": amount.unit.code,
    }
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _pinned(deps: Deps, uow: UnitOfWork, pinned_release_id: str) -> ReleaseIndex:
    """INV-S14: consumable and unit come from the run's pins. A pinned release this hub cannot read fails loud."""
    if pinned_release_id == uow.release.release_id:
        return deps.indexes.get(uow.release)
    if deps.releases is None:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "pinned release unreadable", retryable=False)
    return deps.indexes.get(deps.releases.release(pinned_release_id))


def step_amounts(
    pinned: ReleaseIndex, active: ReleaseIndex, uses: Sequence[SupplyNeed], max_uses: int
) -> tuple[StepUse, ...]:
    """One amount per supply: two needs of one supply in one step add up under one occurrence key. A supply the
    active kit map no longer lists is refused below zero by admission (no stock row)."""
    if len(uses) > max_uses:
        raise UseInvalid((FieldIssue("/uses", "INVALID_VALUE", "more needs than supplies.max_step_uses"),))
    totals: dict[str, Quantity] = {}
    defs: dict[str, SupplyDef] = {}
    for need in uses:
        supply = pinned.supplies.get(need.supply_id)
        if need.per_use is None or supply is None or not supply.consumable:
            continue
        current = active.supplies.get(need.supply_id, supply)
        if current.unit != supply.unit:
            raise UnitMismatch(supply.unit.code, current.unit.code)
        amount = Quantity(need.per_use.value, boundary_unit(need.per_use.unit, supply.unit_word, supply.unit))
        if amount.unit != supply.unit:
            raise UnitMismatch(amount.unit.code, supply.unit.code)
        totals[supply.supply_id] = (
            add(totals[supply.supply_id], amount) if supply.supply_id in totals else amount
        )
        defs[supply.supply_id] = supply
    return tuple(StepUse(defs[s], totals[s]) for s in sorted(totals))


def record_step_uses(
    deps: Deps,
    uow: UnitOfWork,
    *,
    environment_id: str,
    plan_run_id: str,
    step_id: str,
    occurrence: int,
    pinned_release_id: str,
    uses: Sequence[SupplyNeed],
    actor: Actor,
    at: datetime,
) -> Sequence[str]:
    """Raises SupplyBelowZero or UnitMismatch and writes nothing; E28 rolls its whole step back (INV-S10). The same
    occurrence with the same body returns the first IDs; with another body it raises StepUseConflict."""
    facts = current_mission(deps, uow)
    where = partition(facts, environment_id)
    active = deps.indexes.get(uow.release)
    amounts = step_amounts(_pinned(deps, uow, pinned_release_id), active, uses, deps.limits.max_step_uses)
    done: dict[str, str] = {}
    pending: list[StepUse] = []
    for use in amounts:
        key = occurrence_key(plan_run_id, step_id, occurrence, use.supply.supply_id)
        found = deps.repository.by_command_key(uow, key)
        if found is None:
            pending.append(use)
        elif found.request_sha256 != body_sha256(where, use.supply.supply_id, use.quantity):
            raise StepUseConflict(key)
        else:
            done[use.supply.supply_id] = found.supply_use_id
    if done and pending:  # the first call wrote all of its uses at once, so this is another body
        raise StepUseConflict(occurrence_key(plan_run_id, step_id, occurrence, pending[0].supply.supply_id))
    if not pending:
        return tuple(done[s] for s in sorted(done))
    for use in pending:
        if deps.corrupt.has(where, use.supply.supply_id):
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "supply ledger failed verification", retryable=False)
    stock = active.kit.stock.get(environment_id, {})
    heads: Mapping[str, Head] = {
        u.supply.supply_id: h
        for u in pending
        if (h := deps.repository.head(uow, where, u.supply.supply_id)) is not None
    }
    admitted = admit_step(pending, stock, heads)
    for result in admitted:
        stored = _stored(deps, uow, where, result, (plan_run_id, step_id, occurrence), actor, at)
        deps.repository.append(uow, stored)
        deps.repository.register_unit(
            uow, stored.supply_id, stored.quantity.unit.code, first_use_id=stored.supply_use_id
        )
        publish_use(uow, deps.ids, stored, use_body(stored))
        done[stored.supply_id] = stored.supply_use_id
    publish_cover(deps, uow, where, done[admitted[-1].supply_id], at)
    return tuple(done[s] for s in sorted(done))


def _stored(
    deps: Deps,
    uow: UnitOfWork,
    where: Partition,
    result: Admitted,
    step: tuple[str, str, int],
    actor: Actor,
    at: datetime,
) -> StoredUse:
    plan_run_id, step_id, occurrence = step
    key = occurrence_key(plan_run_id, step_id, occurrence, result.supply_id)
    return StoredUse(
        supply_use_id=deps.ids.new("sup"),
        partition=where,
        supply_id=result.supply_id,
        quantity=result.quantity,
        seq=result.seq,
        used_after=result.used_after,
        plan_run_id=plan_run_id,
        help_session_id=None,
        step_id=step_id,
        occurrence=occurrence,
        at=uow.now,
        observed_at=at if at <= uow.now else uow.now,
        by_kind=actor.kind,
        by_id=actor.id,
        release_id=uow.release.release_id,
        command_key=key,
        request_sha256=body_sha256(where, result.supply_id, result.quantity),
    )
