"""Structural E12 boundary, pinned to origin/e12-build public contracts by tests.

The owner's ports import its domain. SYSTEM 6 prohibits importing those class objects here.
E12 must bind an adapter under these consumer-owned Protocol keys before this feature can use it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.uow import SessionContext, UnitOfWork


class Phase(StrEnum):
    INTAKE = "intake"
    ASSESSMENT = "assessment"
    ACQUISITION = "acquisition"
    DISPOSITION = "disposition"
    HELP = "help"
    INSIGHT = "insight"
    GENERAL = "general"
    VERIFIER = "verifier"


@dataclass(frozen=True, slots=True)
class ToolScope:
    session: SessionContext
    subject_person_id: str | None
    mission_id: str
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    episode_id: str | None
    help_session_id: str | None
    plan_run_id: str | None
    turn_id: str
    conversation_id: str
    config_release_id: str
    evidence_release_id: str
    model_release_id: str
    phase: Phase
    source_revision: int
    generation: int
    deadline: ElapsedDeadline


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_tokens: int
    output_reserve: int
    section_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    sections: bytes
    included_ids: tuple[str, ...]
    omitted_sections: tuple[str, ...]
    config_release_id: str
    evidence_release_id: str
    model_release_id: str
    source_revision: int
    snapshot_fingerprint: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    tool_call_id: str
    name: str
    arguments: bytes


@dataclass(frozen=True, slots=True)
class PendingEffect:
    turn_id: str
    tool_call_id: str
    generation: int
    source_revision: int
    arguments: bytes
    references: tuple[str, ...]


type ToolStatus = Literal[
    "ok", "no_result", "unavailable", "declined", "stale", "conflicting", "not_aboard", "denied", "error"
]
type ToolsErrorCode = Literal[
    "MALFORMED_MODEL_OUTPUT",
    "MODEL_OUTPUT_REJECTED",
    "CONFIG_INVALID",
    "CAPABILITY_UNAVAILABLE",
    "PAYLOAD_TOO_LARGE",
]


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    status: ToolStatus
    data: bytes | None
    provenance_ids: tuple[str, ...]
    observed_at: datetime | None
    generated_at: datetime
    limitations: tuple[str, ...]
    error_code: ToolsErrorCode | None


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


class AppliedEffects(Protocol):
    @property
    def request_ids(self) -> tuple[str, ...]: ...
    @property
    def gap_ids(self) -> tuple[str, ...]: ...
    @property
    def measurement_results(self) -> tuple[object, ...]: ...


class ToolGateway(Protocol):
    def execute(self, uow: UnitOfWork, scope: ToolScope, call: ToolCall) -> ToolResult: ...


class ContextBuilderPort(Protocol):
    def build(
        self, uow: UnitOfWork, scope: ToolScope, phase: Phase, budget: ContextBudget
    ) -> ContextSnapshot: ...


class OutputPolicyPort(Protocol):
    def check(self, fields: Sequence[TextField], scope: ToolScope, turn: TurnData) -> PolicyVerdict: ...


class EffectApplier(Protocol):
    def apply(
        self, uow: UnitOfWork, scope: ToolScope, effects: tuple[PendingEffect, ...], idempotency_prefix: str
    ) -> AppliedEffects: ...


class ScopeAuthority(Protocol):
    def require_current(self, uow: UnitOfWork, scope: ToolScope) -> bool: ...
    def authorize_arguments(
        self, uow: UnitOfWork, scope: ToolScope, name: str, arguments: Mapping[str, object]
    ) -> bool: ...
    def authorize_result(
        self, uow: UnitOfWork, scope: ToolScope, name: str, data: Mapping[str, object]
    ) -> bool: ...
