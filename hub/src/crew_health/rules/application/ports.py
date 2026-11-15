"""Public ports of the warnings engine (E24 4.2): what other modules may import (SYSTEM 6, modules-independent).

Bus events published in the transaction of the change (`WarningRaised`, `WarningCleared`, E25 and E29) carry the
stored lifecycle facts only, never a session's projection. Producer inputs are the bus events of E23 and E01
records; E24 maps them to its private domain inputs in `subscriptions.py` (anti-corruption layer).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal, Protocol

from crew_health.kernel.uow import Actor, SessionContext, UnitOfWork
from crew_health.rules.domain.inputs import EvidenceKey, InsightDraft, RecordFacts


@dataclass(frozen=True, slots=True)
class WarningFact:
    """The lifecycle facts of one warning that consumers may read: no steps, notes or person text."""

    warning_id: str
    revision: int
    definition_id: str
    level: Literal["info", "watch", "act", "emergency"]  # internal enum
    scope: Literal["habitat", "person", "crew"]  # internal enum
    person_id: str | None
    environment_id: str
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]  # internal enum
    raised_by: Literal["rule", "sensor_limit", "assistant"]  # internal enum
    hazard_id: str | None
    state: str
    raised_at: datetime
    cleared_at: datetime | None
    clear_reason: str | None


@dataclass(frozen=True, slots=True)
class WarningRaised:
    event_type: ClassVar[str] = "warning.raised"
    warning: WarningFact


@dataclass(frozen=True, slots=True)
class WarningCleared:
    event_type: ClassVar[str] = "warning.cleared"
    warning: WarningFact


@dataclass(frozen=True, slots=True)
class Rejected:
    code: Literal["LEVEL_NOT_ALLOWED", "ACTION_NOT_ALLOWED"]  # internal enum


@dataclass(frozen=True, slots=True)
class Allowed:
    level: Literal["info", "watch"]  # internal enum


@dataclass(frozen=True, slots=True)
class AssistantContext:
    actor_id: str
    person_id: str | None


class WarningQueries(Protocol):
    """E03 Today (`active_visible`, `headline`, `count_active_visible`) and E26 (`active_shared_at_or_above`,
    `visible`). Each opens its own read snapshot."""

    def visible(self, session: SessionContext, warning_id: str) -> bool: ...
    def count_active_visible(self, session: SessionContext) -> int: ...
    def active_shared_at_or_above(self, level: str) -> Sequence[WarningFact]: ...


class WarningCommands(Protocol):
    def attach_routine_overrides(
        self, uow: UnitOfWork, warning_id: str, override_ids: Sequence[str]
    ) -> None: ...


class AssistantWarningGuard(Protocol):
    """The only way the assistant reaches warnings (4.2). It has no success type for an action."""

    def propose_level(
        self, uow: UnitOfWork, ctx: AssistantContext, level: str, topic_id: str | None
    ) -> Rejected | Allowed: ...
    def propose_action(
        self, uow: UnitOfWork, ctx: AssistantContext, warning_id: str, action: str
    ) -> Rejected: ...


class RecordFactsPort(Protocol):
    """Facts about the records an insight cites (existence, owner, origin, state), never their content. Owned by the
    records module (R-E24-14); until it provides it, no insight can be verified and none is stored."""

    def facts(self, uow: UnitOfWork, keys: Sequence[EvidenceKey]) -> Mapping[EvidenceKey, RecordFacts]: ...


class InsightWarningPort(Protocol):
    """What the assistant controller (E13) calls in its own unit of work (4.2, UC-R5, UC-R6)."""

    def validate_and_raise(self, uow: UnitOfWork, draft: InsightDraft) -> WarningFact | None: ...
    def on_insight_dismissed(self, uow: UnitOfWork, insight_id: str, actor: Actor) -> None: ...
    def on_insight_expired(self, uow: UnitOfWork, insight_id: str) -> None: ...
