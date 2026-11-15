# mypy: disable-error-code="type-abstract"
# The SYSTEM 6 registry keys ports by their Protocol classes; mypy flags every Protocol passed as type[P].
"""Registration of the teach module (SYSTEM 6): routes, settings, the skills catalog loader check, the audience
rules, GuidedCheckQueries and the binding that builds the use cases once every port resolves. No side effects at
import."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter

from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.config import ConfigReader, ConfigReleaseReader, ReleaseCheck, TemplateRenderer
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.units import UnitRegistry
from crew_health.kernel.uow import AudienceRule, ReadSnapshotFactory, UnitOfWorkFactory
from crew_health.teach.adapters.catalog_checks import SkillsCatalogCheck
from crew_health.teach.adapters.sqlite_repository import SqliteCoachingSamples, SqliteGuidedChecks
from crew_health.teach.api.router import TeachApi, assemble, build_router
from crew_health.teach.application.audience import CoachingAudience, GuidedCheckAudience
from crew_health.teach.application.commands import AUDIENCE_RULE, TeachPorts
from crew_health.teach.application.content import ContentLoader
from crew_health.teach.application.ports import (
    AnswerCommands,
    CameraReferencePort,
    GuidanceEligibility,
    GuidedCheckQueries,
    HelpSessionReader,
    ObservationCommands,
    PersonDirectory,
    PlanRunReader,
    RecordRouting,
    RequestAuthenticator,
    RoleAssignmentReader,
)
from crew_health.teach.application.queries import GuidedCheckReader
from crew_health.teach.application.reads import COACHING_RULE
from crew_health.teach.application.settings import SPECS, TeachSettings


class Resolver(Protocol):
    """Structural copy of crew_health.app.registry.Resolver: a module never imports the app (app-only-assembles)."""

    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class ModuleRegistry(Protocol):
    """The part of crew_health.app.registry.ModuleRegistry this module declares into (SYSTEM 6)."""

    def router(self, router: APIRouter) -> None: ...
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def audience_rule(self, name: str, rule: AudienceRule) -> None: ...
    def loader_check(self, check: ReleaseCheck) -> None: ...
    def id_prefix(self, *, record_type: str, prefix: str) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...


def register(registry: ModuleRegistry) -> None:
    api = TeachApi()
    coaching = CoachingAudience()
    checks = SqliteGuidedChecks()
    registry.router(build_router(api))
    for spec in SPECS:
        registry.setting(spec)
    registry.loader_check(SkillsCatalogCheck())
    registry.audience_rule(AUDIENCE_RULE, GuidedCheckAudience(checks))
    registry.audience_rule(COACHING_RULE, coaching)
    registry.id_prefix(record_type="guided_check", prefix="gck")
    registry.id_prefix(record_type="coaching_sample", prefix="cs")
    registry.provide(GuidedCheckQueries, lambda _: GuidedCheckReader(checks))

    def bind(resolver: Resolver) -> None:
        settings = TeachSettings.read(resolver.require(HubSettings))
        ports = TeachPorts(
            uow=resolver.require(UnitOfWorkFactory),
            snapshots=resolver.require(ReadSnapshotFactory),
            clock=resolver.require(Clock),
            ids=resolver.require(IdFactory),
            settings=settings,
            releases=resolver.require(ConfigReleaseReader),
            content=ContentLoader(resolver.require(ConfigReader), max_cached=settings.content_cache_max),
            renderer=resolver.require(TemplateRenderer),
            idempotency=resolver.require(IdempotencyStore),
            receipts=resolver.require(CommandReceipts),
            units=resolver.require(UnitRegistry),
            repository=checks,
            observations=resolver.require(ObservationCommands),
            answers=resolver.require(AnswerCommands),
            eligibility=resolver.require(GuidanceEligibility),
            people=resolver.require(PersonDirectory),
            routing=resolver.require(RecordRouting),
            plans=resolver.optional(PlanRunReader),
            camera=resolver.optional(CameraReferencePort),
            roles=resolver.optional(RoleAssignmentReader),
        )
        help_reader = resolver.optional(HelpSessionReader)
        coaching.help = help_reader
        api.services = assemble(
            ports,
            authenticator=resolver.require(RequestAuthenticator),
            samples=SqliteCoachingSamples(),
            help=help_reader,
        )

    registry.bind("teach", bind)
