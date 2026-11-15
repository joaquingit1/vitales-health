"""What a committed use publishes (E32 4.5; SYSTEM 8.1): the in-transaction SupplyCoverChanged batch for E24, the
contracted `supply.used` outbox event after commit, its replication entry and an ID-only audit entry."""

from __future__ import annotations

from collections.abc import Mapping

from crew_health.contracts_gen.enums import EventType, Role
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReplicationChange,
    UnitOfWork,
)
from crew_health.supplies.application.ports import CoverFact, SupplyPartition
from crew_health.supplies.application.release_index import ReleaseIndex
from crew_health.supplies.application.repository import StoredRefusal, StoredUse
from crew_health.supplies.application.wire import Json, encode, instant
from crew_health.supplies.domain.model import CoverRow, Partition
from crew_health.supplies.domain.quantity import decimal_text

AUDIENCE_RULE = "supplies.crew"
CREW_EVENT_ROLES = frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER, Role.STATION})  # supply.used scope crew
PROJECTION = AudienceProjectionKey("supplies", EventType.SUPPLY_USED.value, 1)
POLICY_VERSION = "1"
USE_RECORD = "supply_use"
REFUSAL_RECORD = "supply_use_refusal"
AUDIT_USE = "supplies.use_recorded"
AUDIT_REFUSED = "supplies.use_refused"


def partition_of(partition: Partition) -> SupplyPartition:
    return SupplyPartition(
        partition.mission_id, partition.environment_id, partition.custody_location_id, partition.origin_mode
    )


def quantity_text(row: CoverRow, unknown: str) -> str:
    """Decimal text and the kit-map unit word, for rendering only (E24 compares cover_uses)."""
    if row.quantity_now is None:
        return unknown
    return decimal_text(row.quantity_now.value) + " " + row.supply.unit_word


def cover_fact(row: CoverRow, index: ReleaseIndex) -> CoverFact:
    refs: list[str] = []
    if row.row is not None:
        refs.append(f"kit-map:{index.kit_map_version}#{row.row.pointer}")
    if row.per_use is not None and index.plans_version is not None:
        largest = next(
            n for n in row.used_by if n.per_use is not None and n.per_use.value == row.per_use.value
        )
        refs.append(f"onboard-plans:{index.plans_version}#{largest.pointer}")
    if row.head is not None and row.head.last_use_id is not None:
        refs.append(f"{USE_RECORD}:{row.head.last_use_id}")
    return CoverFact(
        supply_id=row.supply.supply_id,
        supply_name=row.supply.name,
        quantity_now=row.quantity_now,
        per_use=row.per_use,
        cover_uses=row.cover_uses,
        quantity_text=quantity_text(row, index.words.cover_unknown),
        per_use_text=None
        if row.per_use is None
        else decimal_text(row.per_use.value) + " " + row.supply.unit_word,
        last_use_id=None if row.head is None else row.head.last_use_id,
        last_use_at=None if row.head is None else row.head.last_use_at,
        release_id=index.release_id,
        stock_revision=0 if row.head is None else row.head.seq,
        source_refs=tuple(refs),
    )


def use_body(use: StoredUse) -> dict[str, Json]:
    """The SupplyUse of supply-use.schema.json: the quantity is a number in the supply's kit-map unit, which the
    stock view carries; no person data beyond the actor reference."""
    return {
        "supply_use_id": use.supply_use_id,
        "supply_id": use.supply_id,
        "environment_id": use.partition.environment_id,
        "quantity": use.quantity.value,
        "plan_run_id": use.plan_run_id,
        "help_session_id": use.help_session_id,
        "step_id": use.step_id,
        "at": instant(use.at),
        "by": {"kind": use.by_kind, "id": use.by_id, "display_name": None},
        "origin_mode": use.partition.origin_mode,
    }


def _audience(ref: str) -> Audience:
    return Audience(AUDIENCE_RULE, ref, PROJECTION, POLICY_VERSION)


def publish_use(uow: UnitOfWork, ids: IdFactory, use: StoredUse, body: Mapping[str, Json]) -> None:
    """Replication, audit and the `supply.used` outbox event of one stored use, in the writer's transaction."""
    data = encode(body)
    uow.replicate(
        ReplicationChange(
            export_projection=None,
            record_type=USE_RECORD,
            record_id=use.supply_use_id,
            operation="append",
            payload_json=canonical_json({"supply_use_id": use.supply_use_id, "supply_id": use.supply_id}),
            causal_refs=(),
            release_id=use.release_id,
            audience=_audience(use.supply_use_id),
        )
    )
    uow.audit(
        AuditEntry(
            event_id=ids.new("aud"),
            event_type=AUDIT_USE,
            event_version=1,
            actor=uow.actor,
            refs={USE_RECORD: use.supply_use_id},
            correlation_id=uow.correlation_id,
            causation_id=None,
            safe_metadata={},
        )
    )
    uow.emit(
        OutboxEvent(
            type=EventType.SUPPLY_USED,
            occurred_at=use.observed_at,
            scope=EventScope("crew", None, use.partition.environment_id),
            config_release_id=use.release_id,
            correlation_id=uow.correlation_id,
            data_json=data,
            audience=_audience(use.supply_use_id),
        )
    )


def publish_refusal(uow: UnitOfWork, ids: IdFactory, refusal: StoredRefusal, release_id: str) -> None:
    uow.replicate(
        ReplicationChange(
            export_projection=None,
            record_type=REFUSAL_RECORD,
            record_id=refusal.refusal_id,
            operation="append",
            payload_json=canonical_json({"refusal_id": refusal.refusal_id, "supply_id": refusal.supply_id}),
            causal_refs=(),
            release_id=release_id,
            audience=_audience(refusal.refusal_id),
        )
    )
    uow.audit(
        AuditEntry(
            event_id=ids.new("aud"),
            event_type=AUDIT_REFUSED,
            event_version=1,
            actor=uow.actor,
            refs={REFUSAL_RECORD: refusal.refusal_id},
            correlation_id=uow.correlation_id,
            causation_id=None,
            safe_metadata={"code": refusal.code},
        )
    )
