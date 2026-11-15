"""Inward ports of the reports module. T2 implements them; T1 tests them with kernel fakes.

Every port below is local and unexported. Where the shared signature is missing the narrowest protocol is
defined and marked with the PLAN request that settles it. None is an agreed shared API (PLAN P1 to P4)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.private import PrivateReceipt
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.reports.domain.findings import FindingPolicy, FormDefinition
from crew_health.reports.domain.model import ConfirmedFinding, Report


class ReportStore(Protocol):
    """Pending P1 (private read has no PrivateStore method) and P4 (source, quarantine, deletion).

    The adapter builds the PrivateIntent and the keyed identity, then calls PrivateRecordCoordinator. The
    caller supplies only the idempotency `key`. `load` returns None for a missing, revoked or deleted entry.
    """

    def load(self, session: SessionContext, report_id: str) -> Report | None: ...

    def store_new(
        self, session: SessionContext, *, key: str, report: Report, source: bytes
    ) -> PrivateReceipt: ...

    def publish_version(
        self,
        session: SessionContext,
        *,
        key: str,
        report: Report,
        apply: Callable[[UnitOfWork], bool],
    ) -> PrivateReceipt:
        """A new immutable report version. `apply` runs in the one hub transaction; False rejects the
        publication and the version is not attached (coordinator contract, SYSTEM 5.8.1)."""
        ...


class ConfirmationLineage(Protocol):
    """reports_confirmation (hub, clinical lineage only). Pending P1 for its table and DTOs."""

    def advance(self, uow: UnitOfWork, *, report_id: str, expected_revision: int, new_revision: int) -> bool:
        """Compare and set the hub revision of the report. False means another writer advanced it."""
        ...

    def append(
        self, uow: UnitOfWork, findings: tuple[ConfirmedFinding, ...], *, command_key: str
    ) -> None: ...

    def applied(self, view: ReadSnapshot | UnitOfWork, *, command_key: str) -> bool:
        """True once `append` ran for this command key, even with no finding (an all-reject batch)."""
        ...

    def findings_of(
        self, view: ReadSnapshot | UnitOfWork, *, command_key: str
    ) -> tuple[ConfirmedFinding, ...]: ...


class FindingRecorder(Protocol):
    """ObservationCommands-style append in the caller's unit of work. Pending P1 (caller-UoW
    ObservationCommands with trusted report provenance) and P2 (instant mapping for a date-only finding).
    Returns the observation id. Never called for a textual finding."""

    def record(self, uow: UnitOfWork, session: SessionContext, finding: ConfirmedFinding) -> str: ...


class FindingPolicies(Protocol):
    """Indicator code to accepted kind and units. Pending P1/P3 (indicator catalog and unit mapping)."""

    def for_code(self, release: ReleaseHandle, code: str) -> FindingPolicy | None: ...


class FormDefinitions(Protocol):
    """Specialist form definitions. Pending P3: no configuration home exists yet."""

    def get(self, release: ReleaseHandle, form_id: str) -> FormDefinition | None: ...
