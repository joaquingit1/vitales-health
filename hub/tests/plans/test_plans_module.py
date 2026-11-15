"""Plans composition declarations remain usable and closed before late binding."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from crew_health.app.registry import DeclarationRegistry
from crew_health.catchup.application.ports import PlanCatchUpPort
from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.commands import PendingCommand
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.uow import Audience, ReadSnapshot
from crew_health.plans.application.kitmap_copies import KitMapContext, PlanColumnsPort, PlanningDelta
from crew_health.plans.application.measurement_copies import GapPlanPort
from crew_health.plans.application.ports import PlanEscalationReader, PlanRunContextReader
from crew_health.plans.application.teach_copies import GuidedCompletionSink, PlanRunReader
from crew_health.plans.application.tools import PlanTools
from crew_health.plans.module import PlansRuntime, Resolver, register
from crew_health.roles.application import ports as roles_ports

from .plans_stack import session


@pytest.mark.trace("T28-30", "D9", "SYSTEM-5.12")
def test_module_declares_bounded_settings_prefixes_and_every_hook() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=128)
    register(registry)
    declarations = registry.declarations
    settings = declarations["settings"]
    assert len(settings) == 9
    for value in settings.values():
        setting = cast(SettingSpec, value)
        assert setting.maximum is not None
        assert setting.minimum == 1
        assert setting.maximum.is_finite()
        assert setting.basis.startswith("team default:")
        assert setting.kind == "integer"
        assert setting.unit is None
    assert declarations["prefixes"]["prun"] == "plan_run"
    assert declarations["prefixes"]["pes"] == "escalation_intent"
    assert "plans.run_viewers" in declarations["audiences"]
    assert "plans.escalation" in declarations["resumers"]
    assert "plans.onboard_plans" in declarations["loader_checks"]
    assert cast(tuple[object, object], declarations["durable_subscribers"]["plans.help"])[0] == frozenset(
        {EventType.HELP_SESSION_CHANGED}
    )
    assert cast(tuple[object, object], declarations["durable_subscribers"]["plans.handoff"])[0] == frozenset(
        {EventType.HANDOFF_CHANGED}
    )
    assert "plans" in registry.bindings
    assert len(declarations["routers"]) == 1
    assert set(registry.providers) == {
        roles_ports.PlanRunReader,
        PlanRunReader,
        GuidedCompletionSink,
        GapPlanPort,
        PlanColumnsPort,
        PlanRunContextReader,
        PlanCatchUpPort,
        PlanEscalationReader,
        PlanTools,
    }


@pytest.mark.trace("D9", "T28-30", "SYSTEM-5.12")
def test_all_provided_facades_fail_closed_before_binding() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=128)
    register(registry)
    factory, _ = registry.providers[roles_ports.PlanRunReader]
    runtime = cast(PlansRuntime, factory(cast(Resolver, object())))
    calls: tuple[Callable[[], object], ...] = (
        lambda: runtime.state("prun_missing"),
        lambda: runtime.guided_step("prun_missing", "pulse_check_wrist"),
        lambda: runtime.context(session(), "prun_missing"),
        lambda: runtime.plan_for(condition_ids=(), environment_id="lunar_surface", subject_person_id=None),
        lambda: runtime.catch_up_inputs("prun_missing"),
        lambda: runtime.row_columns(cast(KitMapContext, object()), kit=frozenset(), plan_ids=()),
        lambda: runtime.plans_changed(
            cast(KitMapContext, object()), base_kit=frozenset(), delta=PlanningDelta((), (), ())
        ),
        lambda: runtime.read(cast(ReadSnapshot, object()), session(), "pes_missing"),
        lambda: runtime.admits(cast(ReadSnapshot, object()), session(), cast(Audience, object())),
        lambda: runtime.resume(session(), cast(PendingCommand, object())),
    )
    for call in calls:
        with pytest.raises(ProblemError) as caught:
            call()
        assert caught.value.code is ErrorCode.INTERNAL_ERROR
        assert caught.value.retryable


def provide_value(registry: DeclarationRegistry, port: type[object], value: object) -> None:
    from crew_health.app.registry import Resolver as RegistryResolver

    def factory(resolver: RegistryResolver) -> object:
        return value

    registry.provide(port, factory)


@pytest.mark.trace("T28-42", "D9", "SYSTEM-5.12")
def test_real_registry_resolves_and_binds_fresh_consumer_ports(tmp_path: Path) -> None:
    from crew_health.kernel.clock import Clock
    from crew_health.kernel.commands import CommandIntents, IdempotencyStore
    from crew_health.kernel.ids import IdFactory
    from crew_health.kernel.settings import HubSettings
    from crew_health.kernel.testing import MemorySettings
    from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWorkFactory
    from crew_health.plans.application.settings import PlansSettings

    from .plans_stack import PLANS_VALUES, stack

    s = stack(tmp_path)
    wire = s.start()
    registry = DeclarationRegistry(max_ports=64, max_hooks=128)
    register(registry)
    for port, value in (
        (UnitOfWorkFactory, s.db),
        (ReadSnapshotFactory, s.db),
        (Clock, s.clock),
        (IdFactory, s.db.ids),
        (HubSettings, MemorySettings(PlansSettings.specs(), PLANS_VALUES)),
        (IdempotencyStore, s.world.idempotency),
        (CommandIntents, s.world.intents),
        (roles_ports.RosterPort, s.crew),
        (roles_ports.MissionStatePort, s.mission),
    ):
        provide_value(registry, port, value)
    resolver = registry.resolve()
    key: type[object] = roles_ports.PlanRunReader
    reader = cast(roles_ports.PlanRunReader, resolver.require(key))
    state = reader.state(str(wire["plan_run_id"]))
    assert state is not None and state.state == "open"
    assert state.subject_person_id == s.session.person_id
    tools = resolver.require(PlanTools)
    assert tools.world.roster is s.crew
    assert tools.answers is None and tools.passages is None
