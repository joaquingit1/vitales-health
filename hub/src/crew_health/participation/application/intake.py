"""Intake (E22 5.2): a reference-only intent is written inside a SAVEPOINT of the producer's own transaction, so it
commits with the producer's input or not at all and never fails the producer for a participation reason."""

from __future__ import annotations

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import UnitOfWork
from crew_health.participation.application.context import Deps, audit
from crew_health.participation.application.ports import IntakeOutcome
from crew_health.participation.domain.errors import ParticipationError
from crew_health.participation.domain.identity import (
    basis_fields,
    canonical_json,
    intent_key,
    logical_identity,
    payload_hash,
)
from crew_health.participation.domain.types import CompletionBasis


class Intake:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    def record(self, uow: UnitOfWork, basis: CompletionBasis) -> IntakeOutcome:
        """`queued`, `duplicate` (same identity and payload), `conflict` (same identity, different payload: the
        first is kept and the second recorded and audited) or `deferred` (a fault inside the savepoint)."""
        try:
            key = intent_key(logical_identity(basis))
            payload = canonical_json(basis_fields(basis))
            digest = payload_hash(basis)
        except ParticipationError:
            return IntakeOutcome("deferred", None)
        try:
            with uow.savepoint("participation_intake"):
                status = self.deps.store.enqueue(
                    uow.connection,
                    key=key,
                    payload=payload,
                    payload_sha256=digest,
                    origin_mode=basis.origin_mode,
                    patient_id=basis.patient_id,
                    at=uow.now,
                )
                if status == "conflict":
                    audit(
                        self.deps, uow, "participation.intent_conflict", "intent", "participation.intent", key
                    )
        except ParticipationError, ValueError:
            return IntakeOutcome("deferred", key)
        except ProblemError as error:
            if error.code is not ErrorCode.INVALID_VALUE:
                raise
            return IntakeOutcome("deferred", key)
        return IntakeOutcome(status, key)
