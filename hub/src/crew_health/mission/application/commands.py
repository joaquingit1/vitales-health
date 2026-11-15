"""Command plumbing of the mission HTTP operations on E01's ports (SYSTEM 5.8): one unit of work per command,
receipt lookup before any mutable precondition, one durable result per complete identity.

Retry after an acknowledged commit finds the stored receipt and answers DUPLICATE_COMMAND with that receipt in
`current_state` (E01's ledger keeps no public body for these operations). The same key with another operation,
target, parameter or body is a conflict, never a replay: the ledger compares the complete identity.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    CommandReceipts,
    IdempotencyStore,
    StoredResult,
)
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import SessionContext, UnitOfWork, UnitOfWorkFactory
from crew_health.mission.domain.errors import MissionDomainError


@dataclass(frozen=True, slots=True)
class Platform:
    uows: UnitOfWorkFactory
    ledger: IdempotencyStore
    receipts: CommandReceipts


@dataclass(frozen=True, slots=True)
class Applied:
    status: int
    resource_ref: str | None


def problem_of(error: MissionDomainError) -> ProblemError:
    """A typed domain failure on the wire. A code outside the contract's error list is an internal fault."""
    try:
        code = ErrorCode(error.code)
    except ValueError:
        return ProblemError(ErrorCode.INTERNAL_ERROR, error.code, retryable=False)
    retryable = True if code is ErrorCode.INTERNAL_ERROR else None
    return ProblemError(code, error.code, retryable=retryable)


def duplicate(receipt: AcceptedReceipt) -> ProblemError:
    return ProblemError(
        ErrorCode.DUPLICATE_COMMAND,
        ErrorCode.DUPLICATE_COMMAND.value,
        retryable=False,
        current_state={
            "kind": "accepted_command",
            "command_key": receipt.command_key,
            "operation_id": receipt.operation_id,
            "target": receipt.target,
            "receipt_id": receipt.receipt_id,
            "accepted_at": receipt.accepted_at.isoformat().replace("+00:00", "Z"),
        },
    )


def execute(
    platform: Platform,
    session: SessionContext,
    identity: CommandIdentity,
    apply: Callable[[UnitOfWork], Applied],
    *,
    release_header: str | None,
) -> Applied:
    """Run `apply` once. The domain error of a refused command rolls the whole unit of work back."""
    if session.actor.id != identity.actor_ref:
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
    with platform.uows.begin(actor=session.actor, command=identity, correlation_id=identity.key) as uow:
        prior = platform.ledger.lookup(uow, identity)
        if prior is not None:
            raise duplicate(prior.receipt)
        if release_header is not None and release_header != uow.release.release_id:
            raise ProblemError(ErrorCode.CONFIG_VERSION_MISMATCH, ErrorCode.CONFIG_VERSION_MISMATCH.value)
        try:
            applied = apply(uow)
        except MissionDomainError as error:
            raise problem_of(error) from error
        receipt = platform.receipts.accepted(uow, identity)
        platform.ledger.record(
            uow, identity, StoredResult(receipt, applied.status, applied.resource_ref, "accepted", None)
        )
        return applied
