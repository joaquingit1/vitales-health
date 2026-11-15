"""Minimal provider shapes for the optional assistant integration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import JsonValue

from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.uow import SessionContext, UnitOfWork


# Provided by E12 (crew_health.assistant); structural copy until it is on main.
@dataclass(frozen=True)
class ToolScope:
    session: SessionContext
    subject_person_id: str
    episode_id: str | None
    help_session_id: str | None
    plan_run_id: str | None
    release_id: str
    turn_id: str


# Provided by E12 (crew_health.assistant); structural copy until it is on main.
@dataclass(frozen=True)
class HandlerOk:
    result: Mapping[str, JsonValue]


# Provided by E12 (crew_health.assistant); structural copy until it is on main.
@dataclass(frozen=True)
class HandlerNoResult:
    reason: str


# Provided by E12 (crew_health.assistant); structural copy until it is on main.
@dataclass(frozen=True)
class HandlerUnavailable:
    reason: str


# Provided by E12 (crew_health.assistant); structural copy until it is on main.
@dataclass(frozen=True)
class HandlerRejected:
    reason: str


# Provided by E12 (crew_health.assistant); structural copy until it is on main.
type HandlerOutcome = HandlerOk | HandlerNoResult | HandlerUnavailable | HandlerRejected

# Provided by E12 (crew_health.assistant); structural copy until it is on main.
Deadline = ElapsedDeadline


# Provided by E12 (crew_health.assistant); structural copy until it is on main.
class ToolHandler(Protocol):
    name: str
    effect: Literal["read", "compute", "proposal"]

    def handle(
        self, uow: UnitOfWork, scope: ToolScope, arguments: Mapping[str, JsonValue], deadline: Deadline
    ) -> HandlerOutcome: ...


# Provided by E12 (crew_health.assistant); structural copy until it is on main.
class ToolRegistry(Protocol):
    def register(self, name: str, handler: ToolHandler) -> None: ...
    def handler(self, name: str) -> ToolHandler | None: ...


# Provided by E10 (crew_health.evidence); structural copy until it is on main.
class ApprovedPassages(Protocol):
    def passages(
        self, uow: UnitOfWork, release_id: str, source_ids: Sequence[str]
    ) -> Sequence[Mapping[str, JsonValue]]: ...


# Provided by E04/E26 (crew_health.intake/crew_health.help); structural copy until it is on main.
class AnswerScope(Protocol):
    def answer_belongs(
        self, uow: UnitOfWork, answer_id: str, episode_id: str | None, help_session_id: str | None
    ) -> bool: ...
