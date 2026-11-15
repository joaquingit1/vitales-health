from __future__ import annotations

from typing import cast

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.config.adapters.repository import ConfigRepository
from crew_health.config.adapters.verification import ConfigArtifacts
from crew_health.config.application.ports import ConfigReleaseRepository
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.config.domain.templates import TemplateCheck
from crew_health.config.settings import specifications
from crew_health.kernel.config import CandidateRelease, Finding, ReleaseCheck


class BoundCheck:
    def __init__(self, check_id: str) -> None:
        self._check_id = check_id
        self.bound: ReleaseCheck | None = None

    @property
    def check_id(self) -> str:
        return self._check_id

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        if self.bound is None:
            raise ConfigInvalid("required configuration artifacts unavailable")
        return self.bound.check(candidate)


def register(registry: ModuleRegistry) -> None:
    registry.provide(cast(type[object], ConfigReleaseRepository), lambda resolver: ConfigRepository())
    registry.loader_check(TemplateCheck())
    core = tuple(BoundCheck(check_id) for check_id in ("1", "2", "9"))
    for check in core:
        registry.loader_check(check)

    def bind(resolver: Resolver) -> None:
        artifacts = resolver.require(ConfigArtifacts)
        implementations: tuple[ReleaseCheck, ...] = (
            artifacts.schemas,
            artifacts.sources,
            artifacts.indicators,
        )
        for bound, implementation in zip(core, implementations, strict=True):
            bound.bound = implementation

    registry.bind("config.validators", bind)
    registry.id_prefix(record_type="config_release", prefix="release")
    for spec in specifications():
        registry.setting(spec)
