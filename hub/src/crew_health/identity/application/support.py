"""Shared wiring of the identity application: dependencies, clock readings and reference-only audit."""

from __future__ import annotations

import threading
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from crew_health.identity.application.settings import IdentitySettings
from crew_health.identity.application.store import CredentialHasher, IdentityStore, TokenSource
from crew_health.identity.domain.session import Instant
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore, canonical_json
from crew_health.kernel.errors import ErrorCode, FieldError, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    ReadSnapshotFactory,
    ReplicationChange,
    UnitOfWork,
    UnitOfWorkFactory,
)

LOCAL_AUDIENCE = "identity.local"
REFERENCE_PROJECTION = AudienceProjectionKey("identity", "reference", 1)


@dataclass(frozen=True, slots=True)
class AuditDeclaration:
    """One E02 audit event: its reference field, the record types it may name and allowed metadata.

    Each module turns these into platform audit policies and registers them with `registry.audit_policy`
    (R-E02-23); the values are the complete allowlist.
    """

    event_type: str
    reference_field: str
    reference_type: str
    metadata: Mapping[str, frozenset[str | int | bool]]


END_REASON_VALUES: frozenset[str | int | bool] = frozenset(
    {"signed_out", "replaced", "credential_changed", "member_removed", "restarted", "revoked"}
)
KIND_VALUES: frozenset[str | int | bool] = frozenset(
    {"crew", "station", "ground", "operator", "editor", "adapter"}
)

AUDIT_DECLARATIONS: tuple[AuditDeclaration, ...] = (
    AuditDeclaration("identity.session_started", "session", "identity.session", {"kind": KIND_VALUES}),
    AuditDeclaration("identity.session_ended", "session", "identity.session", {"reason": END_REASON_VALUES}),
    AuditDeclaration("identity.credential_set", "principal", "identity.principal", {"kind": KIND_VALUES}),
    AuditDeclaration("identity.roster_applied", "roster", "identity.roster", {}),
)


@dataclass
class KdfSlots:
    """The bounded Argon2 admission of E02 11: at most `identity.auth_concurrency` at once."""

    capacity: int
    semaphore: threading.BoundedSemaphore = field(init=False)

    def __post_init__(self) -> None:
        if not 1 <= self.capacity <= 2:
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)
        self.semaphore = threading.BoundedSemaphore(self.capacity)


@dataclass(slots=True)
class GroundSignIn:
    """Ground sessions exist only on ground or demo profiles (E02 7); closed until the composer opens it."""

    allowed: bool = False


@dataclass(slots=True)
class MissionDays:
    """The injected mission clock (SYSTEM 5.2), bound late by the composer. Absent, or with no mission clock
    version covering the instant, the mission day is unknown and no roster member is current (E02 3.1)."""

    clock: MissionClock | None = None

    def day(self, at: datetime) -> int | None:
        if self.clock is None:
            return None
        try:
            return self.clock.mission_day(at)
        except ProblemError as error:
            # A mission clock with no version for this instant raises CONFIG_INVALID; storage faults stay loud.
            if error.code is ErrorCode.CONFIG_INVALID:
                return None
            raise


@dataclass(frozen=True, slots=True)
class IdentityDeps:
    uows: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    clock: Clock
    ids: IdFactory
    settings: IdentitySettings
    store: IdentityStore
    hasher: CredentialHasher
    tokens: TokenSource
    ledger: IdempotencyStore
    receipts: CommandReceipts
    slots: KdfSlots
    ground: GroundSignIn
    days: MissionDays = field(default_factory=MissionDays)

    def instant(self) -> Instant:
        return Instant(self.clock.now(), self.clock.monotonic_ns(), self.clock.process_epoch())


def normalized_credential(credential: str, *, max_bytes: int) -> str:
    """NFC form, so the same passphrase typed on two keyboards verifies; never truncated."""
    try:
        value = unicodedata.normalize("NFC", credential)
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise malformed("credential") from None
    if not 1 <= size <= max_bytes:
        raise malformed("credential")
    return value


def settable_credential(credential: str, *, max_bytes: int) -> str:
    """A new credential: the sign-in bound plus no control characters and no surrounding spaces."""
    value = normalized_credential(credential, max_bytes=max_bytes)
    if value != value.strip() or any(unicodedata.category(character) == "Cc" for character in value):
        raise ProblemError(
            ErrorCode.INVALID_VALUE,
            ErrorCode.INVALID_VALUE.value,
            field_errors=(FieldError("credential", "INVALID_VALUE", "INVALID_VALUE"),),
        )
    return value


def malformed(field_name: str) -> ProblemError:
    return ProblemError(
        ErrorCode.MALFORMED_REQUEST,
        ErrorCode.MALFORMED_REQUEST.value,
        field_errors=(FieldError(field_name, "MALFORMED_REQUEST", "MALFORMED_REQUEST"),),
    )


def reference(
    uow: UnitOfWork,
    *,
    record_type: str,
    record_id: str,
    operation: Literal["create", "version", "append", "retract"],
) -> None:
    """A local-only reference declaration (S5.7): no payload, no export projection, no recipient."""
    uow.replicate(
        ReplicationChange(
            None,
            record_type,
            record_id,
            operation,
            canonical_json({"record_id": record_id}),
            (),
            uow.release.release_id,
            Audience(LOCAL_AUDIENCE, record_id, REFERENCE_PROJECTION, "1"),
        )
    )


def audit(
    uow: UnitOfWork,
    ids: IdFactory,
    declaration: AuditDeclaration,
    record_id: str,
    metadata: Mapping[str, str | int | bool],
) -> None:
    uow.audit(
        AuditEntry(
            ids.new("audit"),
            declaration.event_type,
            1,
            uow.actor,
            {declaration.reference_field: record_id},
            uow.correlation_id,
            None,
            metadata,
        )
    )


def declaration(event_type: str) -> AuditDeclaration:
    for value in AUDIT_DECLARATIONS:
        if value.event_type == event_type:
            return value
    raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
