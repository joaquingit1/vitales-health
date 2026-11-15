from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.commands import CommandIdentity, StoredResult
from crew_health.kernel.config import CandidateRelease, Finding, ReleaseHandle, ReleaseRevision
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork


class ValidationResult(Protocol):
    """What a stored release keeps of the loader's ValidationResult. Consumers import this file only (SYSTEM 6.4),
    so it names the loader's result structurally instead of importing the loader and, through it, config.domain."""

    @property
    def errors(self) -> tuple[Finding, ...]: ...
    @property
    def warnings(self) -> tuple[Finding, ...]: ...
    @property
    def executed(self) -> tuple[str, ...]: ...


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
