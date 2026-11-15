from __future__ import annotations

from typing import cast

from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.kernel.config import ConfigReleaseReader
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.units import UnitRegistry
from crew_health.records.adapters.repository import RecordRepository
from crew_health.records.application.ports import AnswerCommands, ObservationCommands
from crew_health.records.application.producers import AnswerProducer, ObservationProducer
from crew_health.records.application.seams import MissionStatePort, ObservationPolicies, RecordsStore
from crew_health.records.settings import specs


def _producer(resolver: Resolver) -> ObservationProducer:
    return ObservationProducer(
        repository=RecordRepository(),
        ids=resolver.require(IdFactory),  # type: ignore[type-abstract]
        units=resolver.require(UnitRegistry),  # type: ignore[type-abstract]
        releases=resolver.require(ConfigReleaseReader),  # type: ignore[type-abstract]
    )


def _bind(resolver: Resolver) -> None:
    """The mission (E23) and observation policy providers are optional: absent, appends fail closed on the
    mission and quality falls back to the producer's declaration, never accepted (producers.py)."""
    producer = resolver.optional(cast(type[object], ObservationCommands))
    if isinstance(producer, ObservationProducer):
        producer.bind(
            mission=resolver.optional(MissionStatePort),  # type: ignore[type-abstract]
            policies=resolver.optional(ObservationPolicies),  # type: ignore[type-abstract]
        )


def register(registry: ModuleRegistry) -> None:
    registry.provide(cast(type[object], RecordsStore), lambda resolver: RecordRepository())
    registry.provide(
        cast(type[object], ObservationCommands),
        _producer,
        requires=(
            cast(type[object], IdFactory),
            cast(type[object], UnitRegistry),
            cast(type[object], ConfigReleaseReader),
        ),
    )
    registry.provide(
        cast(type[object], AnswerCommands),
        lambda resolver: AnswerProducer(
            cast(ObservationProducer, resolver.require(cast(type[object], ObservationCommands)))
        ),
        requires=(cast(type[object], ObservationCommands),),
    )
    registry.bind("records.producers", _bind)
    registry.id_prefix(record_type="observation", prefix="obs")
    registry.id_prefix(record_type="answer", prefix="ans")
    for spec in specs():
        registry.setting(spec)
