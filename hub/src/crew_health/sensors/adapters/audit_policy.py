"""Audit policies of this module (S5.9). E01 has no registry method for them, so they are registered on the
database object the unit of work factory is, through the same `register_audit_policy` its own policies use.

An entry names records only by reference (`refs` must exist as declared replicated records) and carries no free
text; metadata values are scalars under allowed keys. The shape mirrors E01's AuditPolicy, which a module cannot
import (SYSTEM 6.4)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import UnitOfWorkFactory


@dataclass(frozen=True)
class ModuleAuditPolicy:
    event_type: str
    metadata: frozenset[str]
    reference_fields: frozenset[str]
    reference_types: Mapping[str, frozenset[str]]

    def validate(self, entry: AuditEntry) -> None:
        bad = ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)
        if entry.event_type != self.event_type or entry.event_version != 1:
            raise bad
        require_id(entry.event_id)
        require_id(entry.actor.id)
        require_id(entry.correlation_id)
        if set(entry.refs) != self.reference_fields or not set(entry.safe_metadata) <= self.metadata:
            raise bad
        for value in entry.refs.values():
            require_id(value)


class _Registers(Protocol):
    def register_audit_policy(self, policy: ModuleAuditPolicy) -> None: ...


def register_policies(factory: UnitOfWorkFactory, policies: tuple[ModuleAuditPolicy, ...]) -> None:
    for policy in policies:
        cast(_Registers, factory).register_audit_policy(policy)


POLICIES: tuple[ModuleAuditPolicy, ...] = (
    ModuleAuditPolicy(
        "sensors.batch_ingested", frozenset(), frozenset({"batch"}), {"batch": frozenset({"sensors_batch"})}
    ),
    ModuleAuditPolicy(
        "sensors.environment_activated",
        frozenset(),
        frozenset({"environment"}),
        {"environment": frozenset({"sensors_environment"})},
    ),
    ModuleAuditPolicy("sensors.registry_loaded", frozenset({"source", "sha256"}), frozenset(), {}),
    ModuleAuditPolicy("sensors.registry_rejected", frozenset({"source", "findings"}), frozenset(), {}),
    ModuleAuditPolicy("sensors.registry_unreadable", frozenset({"source"}), frozenset(), {}),
)
