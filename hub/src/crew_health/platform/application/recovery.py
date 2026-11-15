from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.clock import Clock
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.settings import HubSettings
from crew_health.platform.domain.errors import fail


@dataclass(frozen=True, slots=True)
class CaptureFacts:
    hub_sha256: str
    private_sha256: str
    reference_watermark: int
    audit_head_seq: int
    audit_head_hash: str
    receipt_checkpoint: str
    schema_checkpoint: str
    vault_generation: int
    node_id: str
    incarnation: str
    namespace: str | None
    release_id: str | None


@dataclass(frozen=True, slots=True)
class StagedBackup:
    directory: Path
    hub_path: Path
    private_path: Path
    facts: CaptureFacts
    state: Literal["staged_unverified"] = "staged_unverified"


class HubCapture(Protocol):
    clock: Clock

    def backup_to(self, destination: Path) -> str: ...


class CaptureBarrier(Protocol):
    def backup_barrier(self) -> AbstractContextManager[None]: ...


class PrivateCapture(Protocol):
    def capture(self, destination: Path) -> str: ...
    def verify(self, path: Path, watermark: int) -> None: ...


class CurrentVault(Protocol):
    def generation(self) -> int: ...
    def require_current(self, minimum_generation: int) -> None: ...


class CaptureChecks(Protocol):
    def prepare(self, destination: Path) -> tuple[Path, Path]: ...
    def facts(self, hub_path: Path, *, private_sha256: str, vault_generation: int) -> CaptureFacts: ...
    def require_unchanged(self, path: Path, digest: str) -> None: ...


class BackupVerifier(Protocol):
    """An E20 adapter must verify its own signed envelope before returning facts."""

    def verify(self, directory: Path) -> StagedBackup: ...


class CaptureService:
    """Local staging only. This emits no archive and never acknowledges a restore."""

    def __init__(
        self,
        *,
        hub: HubCapture,
        coordinator: CaptureBarrier,
        private: PrivateCapture,
        repository: CaptureChecks,
        vault: CurrentVault,
        settings: HubSettings,
    ) -> None:
        self.hub, self.coordinator, self.private = hub, coordinator, private
        self.repository, self.vault, self.settings = repository, vault, settings

    def stage(self, destination: Path) -> StagedBackup:
        deadline = self.hub.clock.monotonic_ns() + self.settings.integer("backup.timeout_ms") * 1000000
        hub_path, private_path = self.repository.prepare(destination)
        with self.coordinator.backup_barrier():
            self._check_deadline(deadline)
            generation = self.vault.generation()
            if type(generation) is not int or generation < 0:
                raise fail(ErrorCode.CONFIG_INVALID)
            self.vault.require_current(generation)
            hub_hash = self.hub.backup_to(hub_path)
            self._check_deadline(deadline)
            private_hash = self.private.capture(private_path)
            self._check_deadline(deadline)
            facts = self.repository.facts(hub_path, private_sha256=private_hash, vault_generation=generation)
            self.repository.require_unchanged(hub_path, hub_hash)
            self.repository.require_unchanged(private_path, private_hash)
            self.private.verify(private_path, facts.reference_watermark)
            self.vault.require_current(generation)
        self._check_deadline(deadline)
        return StagedBackup(destination, hub_path, private_path, facts)

    def _check_deadline(self, deadline: int) -> None:
        if self.hub.clock.monotonic_ns() > deadline:
            raise StorageUnavailable("io_error")


class RecoveryService:
    def __init__(
        self,
        *,
        verifier: BackupVerifier | None,
        private: PrivateCapture,
        vault: CurrentVault,
        require_hash: Callable[[Path, str], None] | None = None,
    ) -> None:
        self.verifier, self.private, self.vault, self.require_hash = verifier, private, vault, require_hash

    def verify(self, directory: Path) -> StagedBackup:
        if self.verifier is None or self.require_hash is None:
            raise fail(ErrorCode.CAPABILITY_UNAVAILABLE)
        candidate = self.verifier.verify(directory)
        if (
            candidate.directory != directory
            or candidate.facts.namespace is None
            or candidate.facts.release_id is None
        ):
            raise fail(ErrorCode.CONFIG_INVALID)
        for path, digest in (
            (candidate.hub_path, candidate.facts.hub_sha256),
            (candidate.private_path, candidate.facts.private_sha256),
        ):
            if path.parent != directory:
                raise fail(ErrorCode.CONFIG_INVALID)
            self.require_hash(path, digest)
        self.private.verify(candidate.private_path, candidate.facts.reference_watermark)
        self.vault.require_current(candidate.facts.vault_generation)
        return candidate

    def install(self, candidate: StagedBackup) -> None:
        # R-E01-19 has no settled durable maintenance/fencing and switch binding.
        raise fail(ErrorCode.CAPABILITY_UNAVAILABLE)
