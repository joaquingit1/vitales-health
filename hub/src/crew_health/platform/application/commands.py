from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.commands import (
    CommandIdentity,
    CommandReceipts,
    CommandResumer,
    IdempotencyStore,
    PendingCommand,
    ResultVisibility,
    StoredResult,
)
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork, UnitOfWorkFactory
from crew_health.platform.domain.errors import fail


class CommandStore(IdempotencyStore, CommandReceipts, Protocol):
    def pending(
        self, view: ReadSnapshot | UnitOfWork, identity: CommandIdentity
    ) -> PendingCommand | None: ...


class OperationPolicy:
    def __init__(self, operations: Mapping[str, bool]) -> None:
        self.operations = dict(operations)

    def requires_ledger(self, operation_id: str) -> bool:
        if operation_id not in self.operations:
            raise fail(ErrorCode.CONFIG_INVALID)
        return self.operations[operation_id]


class CommandExecutor:
    def __init__(
        self,
        *,
        transactions: UnitOfWorkFactory,
        ledger: CommandStore,
        visibility: ResultVisibility,
        authorize: Callable[[SessionContext, str], None],
        resumers: Mapping[str, CommandResumer] | None = None,
    ) -> None:
        self.transactions, self.ledger, self.visibility, self.authorize = (
            transactions,
            ledger,
            visibility,
            authorize,
        )
        self.resumers = dict(resumers or {})

    def execute(
        self, session: SessionContext, identity: CommandIdentity, apply: Callable[[UnitOfWork], StoredResult]
    ) -> StoredResult:
        self.authorize(session, identity.operation)
        if session.actor.id != identity.actor_ref:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        # Repeat under the writer lock before any mutable business precondition.
        with self.transactions.begin(
            actor=session.actor, command=identity, correlation_id=identity.key
        ) as uow:
            prior = self.ledger.lookup(uow, identity)
            if prior is not None:
                return self._replay(uow, session, identity, prior)
            pending = self.ledger.pending(uow, identity)
            if pending is None:
                result = apply(uow)
                self.ledger.record(uow, identity, result)
                return result
        # Multi-transaction resumptions must never inherit the active hub writer.
        resumer = self.resumers.get(pending.resume_handler)
        if resumer is None:
            raise fail(ErrorCode.INTERNAL_ERROR)
        returned = resumer.resume(session, pending)
        if returned is None:
            raise fail(ErrorCode.INTERNAL_ERROR)
        with self.transactions.begin(
            actor=session.actor, command=identity, correlation_id=identity.key
        ) as uow:
            parent = self.ledger.lookup(uow, identity)
            if parent is None or parent.receipt != returned.receipt:
                raise fail(ErrorCode.INTERNAL_ERROR)
            return self._replay(uow, session, identity, parent)

    def _replay(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        identity: CommandIdentity,
        prior: StoredResult,
    ) -> StoredResult:
        self.visibility.authorize(view, session, identity, prior)
        if prior.safe_body is not None and view.now - prior.receipt.accepted_at <= timedelta(hours=24):
            return prior
        receipt = prior.receipt
        raise ProblemError(
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
