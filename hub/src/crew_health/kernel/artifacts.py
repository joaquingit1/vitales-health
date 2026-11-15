from __future__ import annotations

import re
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    sha256: str
    byte_count: int

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"[a-f0-9]{64}", self.sha256) is None
            or type(self.byte_count) is not int
            or self.byte_count < 0
        ):
            raise ProblemError(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    receipt_id: str
    reference: ArtifactReference

    def __post_init__(self) -> None:
        require_id(self.receipt_id)


class ArtifactReader(Protocol):
    def read(self, reference: ArtifactReference) -> bytes: ...


class DurableArtifactStore(ArtifactReader, Protocol):
    """Shared provider owns publication, quarantine, reference barriers and paired backups.

    Publish returns only after the complete hash path is durable on the host platform.
    Pin holds a verified immutable reference through the caller's transaction; reclamation
    cannot remove it. Attach records the reference in that UoW and joins the shared backup
    barrier. A receipt alone never acknowledges a feature command. Storage/integrity faults
    propagate; accepted command completion remains owned by the shared command executor.
    """

    def publish(self, reference: ArtifactReference, content: bytes) -> PublicationReceipt: ...
    def pin(self, reference: ArtifactReference) -> AbstractContextManager[ArtifactReference]: ...
    def attach(self, uow: UnitOfWork, receipt: PublicationReceipt, *, owner_ref: str) -> None: ...
