from __future__ import annotations

from typing import cast

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.config.adapters.readers import UI_COPY_CACHE_SETTING, ConfigReaders, UnknownTextCache
from crew_health.config.adapters.repository import ConfigRepository
from crew_health.config.adapters.units import ReleaseWireUnitConverter
from crew_health.config.adapters.verification import ConfigArtifacts
from crew_health.config.application.ports import ConfigReleaseRepository
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.config.domain.package_checks import PACKAGE_CHECK_IDS
from crew_health.config.domain.templates import TemplateCheck
from crew_health.config.settings import specifications
from crew_health.kernel.config import (
    CandidateRelease,
    ConfigBinding,
    ConfigReader,
    ConfigReleaseReader,
    Finding,
    ReleaseCheck,
    TemplateRenderer,
)
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.units import UnitRegistry, WireUnitConverter
from crew_health.kernel.uow import ReadSnapshotFactory


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
    registry.provide(
        WireUnitConverter,  # type: ignore[type-abstract]
        lambda resolver: ReleaseWireUnitConverter(resolver.require(UnitRegistry)),  # type: ignore[type-abstract]
        requires=(UnitRegistry,),
    )
    registry.provide(cast(type[object], ConfigReleaseRepository), lambda resolver: ConfigRepository())
    registry.provide(
        ConfigReaders,
        lambda resolver: ConfigReaders(
            resolver.require(ReadSnapshotFactory),  # type: ignore[type-abstract]
            ConfigRepository(),
            UnknownTextCache(
                resolver.require(HubSettings).integer(UI_COPY_CACHE_SETTING)  # type: ignore[type-abstract]
            ),
        ),
        requires=(ReadSnapshotFactory, HubSettings),
    )
    for port in (ConfigBinding, ConfigReader, ConfigReleaseReader, TemplateRenderer):
        registry.provide(
            cast(type[object], port),
            lambda resolver: resolver.require(ConfigReaders),
            requires=(ConfigReaders,),
        )
    registry.loader_check(TemplateCheck())
    core = tuple(BoundCheck(check_id) for check_id in ("1", "2", "9"))
    package = tuple(BoundCheck(check_id) for check_id in PACKAGE_CHECK_IDS)
    for check in (*core, *package):
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
        # Package checks bind only when the artifacts carry them; an unbound one fails closed at validation.
        supplied = {check.check_id: check for check in artifacts.package}
        for bound in package:
            bound.bound = supplied.get(bound.check_id)

    registry.bind("config.validators", bind)
    registry.id_prefix(record_type="config_release", prefix="release")
    for spec in specifications():
        registry.setting(spec)
