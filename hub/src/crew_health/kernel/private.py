from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from crew_health.kernel.errors import ProblemError

if TYPE_CHECKING:
    from crew_health.kernel.commands import CommandIdentity
    from crew_health.kernel.errors import ProblemError
    from crew_health.kernel.uow import SessionContext, UnitOfWork


@dataclass(frozen=True, slots=True)
class PrivateIntent:
    identity: CommandIdentity
    owner_person_id: str
    entry_id: str
    record_kind: str
    version_id: str
    source_version_id: str | None
    plaintext: bytes


@dataclass(frozen=True, slots=True)
class SealedPrivateRef:
    entry_id: str
    version_id: str
    record_kind: str
    owner_person_id: str
    command_key: str
    fingerprint: str
    key_id: str
    ciphertext_sha256: str


@dataclass(frozen=True, slots=True)
class PrivateReceipt:
    entry_id: str
    command_key: str
    hub_reference_seq: int


class PrivateStore(Protocol):
    def seal(self, intent: PrivateIntent) -> SealedPrivateRef: ...
    def verify(self, reference: SealedPrivateRef) -> bool: ...
    def acknowledge(self, reference: SealedPrivateRef, receipt: PrivateReceipt) -> None: ...
    def discard_orphan(self, reference: SealedPrivateRef) -> None: ...
    def pending(self, *, after_entry_id: str | None, limit: int) -> tuple[SealedPrivateRef, ...]: ...


@dataclass(frozen=True, slots=True)
class RevocationReceipt:
    revocation_id: str
    generation: int
    complete: bool
    outstanding_replica_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PrivateDeletionResult:
    entry_ids: tuple[str, ...]
    command_key: str
    revocation: RevocationReceipt
    hub_reference_seq: int | None


class PrivatePublicationRejected(ProblemError):
    def __init__(self) -> None:
        from crew_health.contracts_gen.enums import ErrorCode

        super().__init__(ErrorCode.INVALID_TRANSITION, ErrorCode.INVALID_TRANSITION.value)


class PrivateRecordCoordinator(Protocol):
    def publish(
        self,
        session: SessionContext,
        intent: PrivateIntent,
        reference: SealedPrivateRef,
        apply: Callable[[UnitOfWork], bool],
    ) -> PrivateReceipt: ...
    def write(self, session: SessionContext, intent: PrivateIntent) -> PrivateReceipt: ...
    def delete(
        self,
        session: SessionContext,
        *,
        identity: CommandIdentity,
        owner_person_id: str,
        entry_ids: tuple[str, ...],
    ) -> PrivateDeletionResult: ...
    def recover(self, *, limit: int) -> int: ...
    def backup_barrier(self) -> AbstractContextManager[None]: ...


class PrivateReferenceWriter(Protocol):
    def attach(self, uow: UnitOfWork, reference: SealedPrivateRef, intent: PrivateIntent) -> None: ...
    def tombstone(self, uow: UnitOfWork, *, entry_ids: tuple[str, ...], revocation_ref: str) -> None: ...


class PrivateKeyVault(Protocol):
    def create_entry_key(self, *, owner_person_id: str, entry_id: str) -> str: ...
    def revoke(
        self, *, owner_person_id: str, entry_ids: tuple[str, ...], command_key: str
    ) -> RevocationReceipt: ...
    def revocation(self, revocation_id: str) -> RevocationReceipt: ...
    def is_revoked(self, *, entry_id: str) -> bool: ...
    def fingerprint(self, *, owner_person_id: str, canonical_identity_and_body: bytes) -> str: ...
    def encrypt(self, *, key_id: str, plaintext: bytes, associated_data: bytes) -> SealedBlob: ...
    def decrypt(
        self,
        session: SessionContext,
        *,
        reference: SealedPrivateRef,
        blob: SealedBlob,
        associated_data: bytes,
    ) -> bytes: ...


@dataclass(frozen=True, slots=True)
class SealedBlob:
    nonce: bytes
    ciphertext: bytes
    wrapped_dek: bytes
