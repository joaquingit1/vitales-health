from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.contracts_gen.schemas.model_action_schema import ModelAction
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.scheduler import ElapsedDeadline as Deadline
from crew_health.kernel.uow import UnitOfWork
from crew_health.measurement.application.ports import ProposalResult
from crew_health.tools.domain.context import ContextBudget, ContextSection, ContextSnapshot
from crew_health.tools.domain.denials import DenialReason
from crew_health.tools.domain.errors import ToolsErrorCode
from crew_health.tools.domain.phase import Phase
from crew_health.tools.domain.scope import ToolScope
from crew_health.tools.domain.tool_result import PendingEffect, ToolCall, ToolResult


@dataclass(frozen=True, slots=True)
class HandlerOk:
    data: bytes
    provenance_ids: tuple[str, ...]
    observed_at: datetime | None
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HandlerNoResult:
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HandlerUnavailable:
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HandlerRejected:
    reason: DenialReason


type HandlerOutcome = HandlerOk | HandlerNoResult | HandlerUnavailable | HandlerRejected


class ToolHandler(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def effect(self) -> Literal["read", "compute", "proposal"]: ...
    def handle(
        self, uow: UnitOfWork, scope: ToolScope, arguments: Mapping[str, object], deadline: Deadline
    ) -> HandlerOutcome: ...


class ToolRegistry(Protocol):
    def register(self, name: str, handler: ToolHandler) -> None: ...
    def handler(self, name: str) -> ToolHandler | None: ...


class ToolGateway(Protocol):
    def execute(self, uow: UnitOfWork, scope: ToolScope, call: ToolCall) -> ToolResult: ...


class ScopeAuthority(Protocol):
    """Controller binding rechecks session, pins, source revisions and task generation in this UOW."""

    def require_current(self, uow: UnitOfWork, scope: ToolScope) -> bool: ...
    def authorize_arguments(
        self, uow: UnitOfWork, scope: ToolScope, name: str, arguments: Mapping[str, object]
    ) -> bool: ...
    def authorize_result(
        self, uow: UnitOfWork, scope: ToolScope, name: str, data: Mapping[str, object]
    ) -> bool: ...


class ArgumentValidator(Protocol):
    @property
    def model_release_id(self) -> str: ...
    def effect(self, name: str) -> Literal["read", "compute", "proposal"] | None: ...
    def exposed(self, name: str, phase: str) -> bool: ...
    def parse(self, name: str, raw: bytes) -> Mapping[str, object]: ...
    def validate_result(self, name: str, raw: bytes) -> Mapping[str, object]: ...


class AuthorizedContextPort(Protocol):
    """Owner projection boundary; implementations must reauthorize every object and field in this view."""

    def sections(self, uow: UnitOfWork, scope: ToolScope, phase: Phase) -> tuple[ContextSection, ...]: ...


class ContextBuilderPort(Protocol):
    def build(
        self, uow: UnitOfWork, scope: ToolScope, phase: Phase, budget: ContextBudget
    ) -> ContextSnapshot: ...


class TokenCounter(Protocol):
    def count(self, prompt: bytes, *, model_release_id: str) -> int: ...


class PromptSerializer(Protocol):
    def serialize(self, scope: ToolScope, phase: Phase, sections: bytes) -> bytes: ...


class ContextIdentity(Protocol):
    def fingerprint(self, *, owner_person_id: str, canonical_identity_and_body: bytes) -> str: ...


class EffectValidation(Protocol):
    def validate(self, uow: UnitOfWork, scope: ToolScope, action: ModelAction) -> None: ...


@dataclass(frozen=True, slots=True)
class AppliedEffects:
    request_ids: tuple[str, ...]
    gap_ids: tuple[str, ...]
    measurement_results: tuple[ProposalResult, ...]


class EffectApplier(Protocol):
    def apply(
        self, uow: UnitOfWork, scope: ToolScope, effects: tuple[PendingEffect, ...], idempotency_prefix: str
    ) -> AppliedEffects: ...


@dataclass(frozen=True, slots=True)
class TextField:
    field: str
    text: str
    source_ref: str | None


@dataclass(frozen=True, slots=True)
class TurnData:
    release: ReleaseHandle
    source_revision: int
    authored_fields: tuple[TextField, ...]


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    allowed: bool
    error_code: ToolsErrorCode | None


class OutputPolicyPort(Protocol):
    def check(self, fields: Sequence[TextField], scope: ToolScope, turn: TurnData) -> PolicyVerdict: ...
