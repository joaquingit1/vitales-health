from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from crew_health.app.registry import DeclarationRegistry, Resolver
from crew_health.episodes.application import ports
from crew_health.episodes.application.dto import AnswerFact, NewEpisode, PersonRef
from crew_health.episodes.application.rules import EpisodeRulesSource
from crew_health.episodes.domain.onset import Onset
from crew_health.episodes.module import Registry, register, setting_defaults, setting_specs
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemoryHealth, MemorySettings
from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWorkFactory
from crew_health.plans.application.ports import EpisodeReader as PlanEpisodeReader
from crew_health.roles.application.ports import ClinicalAuthorityQuery

from .builders import NOW, PATIENT, station


@pytest.mark.trace("T-E15-035", "T-E15-046")
def test_real_registration_resolves_exact_consumer_classes_and_dispatches(tmp_path: Path) -> None:
    state = station(tmp_path)
    registry = DeclarationRegistry(max_ports=100, max_hooks=200)
    providers: dict[type[object], object] = {
        HubSettings: MemorySettings(setting_specs(), setting_defaults()),
        IdFactory: state.db.ids,
        UnitOfWorkFactory: state.db,
        ReadSnapshotFactory: state.db,
        IdempotencyStore: state.service.ledger,
        EpisodeRulesSource: state.service.rules,
        ClinicalAuthorityQuery: state.facts,
        ports.RecordAuthorization: state.facts,
        ports.AnswerStore: state.facts,
        ports.UrgencyQueries: state.facts,
        ports.OriginNamespace: state.facts,
        ports.RosterPort: state.facts,
        ports.ConsentQueries: state.facts,
        ports.QuestionSensitivity: state.facts,
        HealthState: MemoryHealth(maximum=8),
    }
    for port, value in providers.items():

        def factory(resolver: Resolver, value: object = value) -> object:
            return value

        registry.provide(port, factory)
    register(cast(Registry, registry))
    for port in (
        ports.EpisodeKernel,
        ports.EpisodeFacts,
        ports.EpisodeFactsPort,
        ports.RefCheckPort,
        ports.EpisodeReader,
        ports.SelectedEpisodeFacts,
        PlanEpisodeReader,
        ports.EpisodeRecordsSource,
    ):
        assert port in registry.providers
    assert ClinicalAuthorityQuery in registry.providers
    assert ports.RecordAuthorization in registry.providers
    resolver = registry.resolve()
    kernel = resolver.require(ports.EpisodeKernel)  # type: ignore[type-abstract]
    facts = resolver.require(ports.EpisodeFacts)  # type: ignore[type-abstract]
    records = resolver.require(ports.EpisodeRecordsSource)  # type: ignore[type-abstract]
    from collections.abc import Callable

    from crew_health.kernel.uow import DomainEvent, UnitOfWork

    for registration in registry.declarations["subscriptions"].values():
        event_type, handler = cast(
            tuple[type[DomainEvent], Callable[[UnitOfWork, DomainEvent], None]], registration
        )
        state.db.bus.subscribe(event_type, handler, name="test_" + event_type.__name__)
    with state.db.begin(actor=PATIENT.actor, correlation_id="corr_kernel") as uow:
        ref = kernel.create(
            uow,
            NewEpisode(
                patient_id="person_a",
                mission_id="mission_one",
                origin_mode="SIM",
                concern_text="token",
                onset=Onset(None, "unknown"),
                created_by=PersonRef("person_a", "A"),
                questionnaire_version="0" * 64,
                red_flag_ids=(),
                config_release_id="release_1",
                received_at=NOW,
            ),
        )
        uow.publish(AnswerFact(ref.episode_id, "ans_event", "red_one", "SIM", NOW))
        fact = facts.get(uow, ref.episode_id)
        assert fact is not None and fact.state == "open"
        assert records.items(uow, "person_a", None, 20)["items"]
    selected = resolver.require(ports.SelectedEpisodeFacts)  # type: ignore[type-abstract]
    plans = resolver.require(PlanEpisodeReader)  # type: ignore[type-abstract]
    with state.db.snapshot() as view:
        snapshot = selected.read(view, PATIENT, episode_id=ref.episode_id, fields=frozenset({"concern_text"}))
        assert snapshot.subject_person_id == "person_a" and snapshot.fields[0].value_text == "token"
    context = plans.visible(PATIENT, ref.episode_id)
    assert context is not None and context.subject_person_id == "person_a"
    assert "episodes.maintenance" in registry.declarations["scheduler_handlers"]
