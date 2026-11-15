from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.commands import CommandIdentity, StoredResult
from crew_health.kernel.config import CandidateRelease, ReleaseHandle, ReleaseRevision
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork

if TYPE_CHECKING:
    from crew_health.config.application.loader import ValidationResult


@dataclass(frozen=True, slots=True)
class TextMatches:
    release_id: str
    state: Literal["checked", "unavailable"]
    spans: tuple[tuple[int, int], ...]


class TextRulesPort(Protocol):
    def find_amounts(self, text: str, *, release: ReleaseHandle) -> TextMatches: ...
    def find_medicines(self, text: str, *, release: ReleaseHandle) -> TextMatches: ...


class ConfigReleaseRepository(Protocol):
    def release(self, view: ReadSnapshot, release_id: str) -> ReleaseHandle: ...
    def active_id(self, view: ReadSnapshot) -> str: ...
    def generation(self, view: ReadSnapshot) -> int: ...
    def loaded_at(self, view: ReadSnapshot, name: ConfigName, version: str) -> str: ...
    def revision(self, view: ReadSnapshot, release_id: str) -> ReleaseRevision: ...
    def store(
        self, uow: UnitOfWork, candidate: CandidateRelease, validation: ValidationResult, *, note: str | None
    ) -> None: ...
    def wire(self, view: ReadSnapshot, release_id: str) -> dict[str, object]: ...
    def list_ids(self, view: ReadSnapshot, *, after: str | None, limit: int) -> tuple[str, ...]: ...


class CommandExecution(Protocol):
    def execute(
        self,
        session: SessionContext,
        identity: CommandIdentity,
        apply: Callable[[UnitOfWork], StoredResult],
    ) -> StoredResult: ...
