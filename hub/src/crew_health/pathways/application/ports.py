"""Canonical E11 public class keys; stdlib, kernel and pure application DTO imports only."""

from typing import Protocol

from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.pathways.application.dto import (
    AssistantContext,
    Disposition,
    GateDecision,
    GateResult,
    PathwayContext,
    PathwayEvaluation,
    SignalPage,
    TurnContext,
    UrgencyState,
    UrgencySummary,
    UrgencyToken,
)


class UrgencyQueries(Protocol):
    def summary(self, uow: ReadSnapshot | UnitOfWork, episode_id: str) -> UrgencySummary: ...
    def signals(
        self, uow: ReadSnapshot | UnitOfWork, episode_id: str, cycle: int, after_seq: int, limit: int
    ) -> SignalPage: ...
    def blocks_resolution(self, uow: UnitOfWork, episode_id: str) -> bool: ...


class UrgencyCommands(Protocol):
    def request_review_from_model(self, uow: UnitOfWork, ctx: AssistantContext) -> UrgencyState: ...
    def start_response(
        self, session: SessionContext, episode_id: str, *, if_match: int, idempotency_key: str
    ) -> dict[str, object]: ...
    def record_disposition(
        self,
        session: SessionContext,
        episode_id: str,
        cycle: int,
        cycle_revision: int,
        disposition: Disposition,
        rationale: str,
        *,
        if_match: int,
        idempotency_key: str,
    ) -> dict[str, object]: ...


class InferenceGate(Protocol):
    def before_turn(self, uow: UnitOfWork, ctx: TurnContext) -> GateDecision: ...
    def still_valid(self, uow: UnitOfWork, token: UrgencyToken) -> bool: ...


class AssistantTurnControl(Protocol):
    def cancel_episode_turns(self, episode_id: str, reason: str) -> None: ...


class RequestGatePort(Protocol):
    def check(
        self, uow: UnitOfWork, capability_code: str, subject_id: str, operator_id: str | None
    ) -> GateResult: ...


class PathwayQueries(Protocol):
    def get_pathway(
        self, uow: ReadSnapshot | UnitOfWork, condition_id: str, ctx: PathwayContext
    ) -> PathwayEvaluation: ...
