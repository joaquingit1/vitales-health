# mypy: disable-error-code="type-abstract"
"""T28-42 and SYSTEM 8.1: resolve real owner modules through the production registry."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from crew_health.app.registry import DeclarationRegistry, Resolver
from crew_health.catchup.application.ports import PlanCatchUpInputs, PlanCatchUpPort
from crew_health.catchup.module import CatchUpRuntime
from crew_health.catchup.module import register as register_catchup
from crew_health.contracts_gen.enums import ConfigName
from crew_health.groundlink.application.ports import GroundLinkQuery
from crew_health.groundlink.application.ports import MissionStatePort as LinkMission
from crew_health.groundlink.module import register as register_groundlink
from crew_health.guidance.application.ports import ContentFacts, EnvironmentFacts, SkillFacts, StockFacts
from crew_health.guidance.module import register as register_guidance
from crew_health.help.module import register as register_help
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandIntents, CommandReceipts, IdempotencyStore
from crew_health.kernel.config import ConfigReader, ConfigReleaseReader, TemplateRenderer
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.testing import MemoryConfig, MemorySettings, MemoryTemplateRenderer, MemoryUnits
from crew_health.kernel.units import Unit, UnitRegistry
from crew_health.kernel.uow import (
    DomainEvent,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.kitmap.application.ports import KitMapContext, KitMapRelease, PlanColumnsPort, PlanningDelta
from crew_health.kitmap.module import register as register_kitmap
from crew_health.measurement.application.ports import GapPlanPort
from crew_health.measurement.module import register as register_measurement
from crew_health.plans.application import ports as plans_ports
from crew_health.plans.application.ground_log import InTimeWords
from crew_health.plans.application.ports import (
    EpisodeReader,
    ObservationRefReader,
    StockReader,
    SupplyCoverPort,
    SupplyLedgerPort,
)
from crew_health.plans.application.tools import PlanTools
from crew_health.plans.module import register as register_plans
from crew_health.roles.application.ports import MissionStatePort, RosterPort
from crew_health.teach.adapters.sqlite_repository import SqliteGuidedChecks
from crew_health.teach.application import ports as teach
from crew_health.teach.domain.guided_check import GuidedCheck
from crew_health.teach.module import PlansComposition
from crew_health.teach.module import register as register_teach

from .plans_stack import NOW, PLANS_VALUES, ROOT, GuidanceStock, PlansStack, load_json, run_step, stack
from .plans_stack import EnvironmentFacts as EnvironmentProvider
from .test_plans_act import scratch_supply_step
from .test_plans_ground_log import Words
from .test_plans_module import provide_value
from .test_plans_provided import ReleaseView


class TeachBoundaries:
    """Unused identity/record boundaries required at teach startup, never substitutes for teach or plans."""

    def canonical_unit(self, code: str) -> Unit:
        raise AssertionError("record writer not used by event publication")

    def append_observation(
        self, uow: UnitOfWork, draft: teach.ObservationDraft, *, idempotency_key: str
    ) -> str:
        raise AssertionError("record writer not used by event publication")

    def append_answer(self, uow: UnitOfWork, draft: teach.AnswerDraft, *, idempotency_key: str) -> str:
        raise AssertionError("answer writer not used by event publication")

    def person(self, person_id: str) -> None:
        return None

    def record_origin(self, person_id: str) -> teach.OriginMode:
        return "SIM"

    def record_for(self, person_id: str, origin: teach.OriginMode) -> str:
        return person_id

    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext:
        raise AssertionError("HTTP is not used by event publication")


@dataclass
class Composed:
    stack: PlansStack
    registry: DeclarationRegistry
    resolver: Resolver


def compose(s: PlansStack, *, plans: bool = True) -> Composed:
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    for register in (
        register_teach,
        register_kitmap,
        register_measurement,
        register_catchup,
        register_groundlink,
        register_guidance,
        register_help,
    ):
        register(registry)
    if plans:
        register_plans(registry)
    specs = tuple(cast(SettingSpec, value) for value in registry.declarations["settings"].values())
    values: dict[str, object] = {
        spec.key: False if spec.kind == "boolean" else int(spec.maximum or Decimal(1)) for spec in specs
    }
    values.update({key: value for key, value in PLANS_VALUES.items() if key in values})
    s.replace_document(
        ConfigName.ENVIRONMENT_HAZARDS, load_json(ROOT / "docs/product/environment-hazards.json")
    )
    config = MemoryConfig([s.release], active_id=s.release.release_id)
    boundaries = TeachBoundaries()
    for port, value in (
        (HubSettings, MemorySettings(specs, values)),
        (UnitOfWorkFactory, s.db),
        (ReadSnapshotFactory, s.db),
        (Clock, s.clock),
        (IdFactory, s.db.ids),
        (IdempotencyStore, s.world.idempotency),
        (CommandReceipts, s.world.idempotency),
        (CommandIntents, s.world.intents),
        (ConfigReader, config),
        (ConfigReleaseReader, config),
        (TemplateRenderer, MemoryTemplateRenderer("Unknown", {})),
        (UnitRegistry, MemoryUnits({})),
        (MissionStatePort, s.mission),
        (LinkMission, s.mission),
        (RosterPort, s.crew),
        (SkillFacts, s.crew),
        (ContentFacts, s.content),
        (EnvironmentFacts, EnvironmentProvider(s.mission)),
        (StockFacts, GuidanceStock(s.supplies)),
        (EpisodeReader, s.episodes),
        (ObservationRefReader, s.observations),
        (StockReader, s.supplies),
        (SupplyCoverPort, s.supplies),
        (SupplyLedgerPort, s.supplies),
        (InTimeWords, Words()),
        (teach.ObservationCommands, boundaries),
        (teach.AnswerCommands, boundaries),
        (teach.PersonDirectory, boundaries),
        (teach.RecordRouting, boundaries),
        (teach.RequestAuthenticator, boundaries),
    ):
        provide_value(registry, port, value)
    # Migrate the actual owner stores before real bindings (including help's recovery worker).
    for owner in ("teach", "help", "groundlink", "catchup"):
        for path in sorted((ROOT / "hub/src/crew_health" / owner / "adapters/migrations").glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            s.db.migrate_tracked(owner + "." + path.stem, hashlib.sha256(sql.encode()).hexdigest(), sql)
    return Composed(s, registry, registry.resolve())


@pytest.fixture(autouse=True)
def no_background_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    from crew_health.help.application.worker import HelpWorker

    monkeypatch.setattr(HelpWorker, "start", lambda self: None)


@pytest.fixture
def composed(tmp_path: Path) -> Composed:
    return compose(stack(tmp_path))


@pytest.mark.trace("T28-42", "SYSTEM-5.12")
def test_real_owner_modules_resolve_plans_providers(composed: Composed) -> None:
    s, registry, resolver = composed.stack, composed.registry, composed.resolver
    run = s.start()
    run_id = str(run["plan_run_id"])
    for key in (plans_ports.GuidedCheckQueries, PlanColumnsPort, GapPlanPort, PlanCatchUpPort):
        assert key in registry.providers, key
    # teach/module.py composes teach's PlanRunReader seam with plans' copy (plans cannot import teach's ports).
    composition = resolver.require(plans_ports.GuidedCheckQueries)
    assert isinstance(composition, PlansComposition)
    assert composition.steps is resolver.require(plans_ports.PlanRunReader)
    guided = composition.guided_step(run_id, "pulse_check_wrist")
    assert isinstance(guided, teach.GuidedStepRef)
    assert guided.step_id == "icl10_pulse_by_crewmate" and guided.step_runnable
    gap = resolver.require(GapPlanPort).plan_for(
        condition_ids=("ICL10",), environment_id="lunar_surface", subject_person_id="mara"
    )
    assert gap is not None and gap.plan_id == "plan_icl10"
    with s.db.snapshot() as view:
        ctx = KitMapContext(
            view, cast(KitMapRelease, ReleaseView(s.release.release_id)), "lunar_surface", NOW
        )
        columns = resolver.require(PlanColumnsPort).row_columns(
            ctx, kit=frozenset(), plan_ids=("plan_icl10", None)
        )
        assert columns[0].plan_id == "plan_icl10" and columns[1].plan_id is None
        assert (
            resolver.require(PlanColumnsPort).plans_changed(
                ctx, base_kit=frozenset(), delta=PlanningDelta((), (), ())
            )
            == ()
        )
    catchup = resolver.require(PlanCatchUpPort).catch_up_inputs(run_id)
    assert isinstance(catchup, PlanCatchUpInputs) and catchup.watch_steps
    assert isinstance(catchup.watch_steps[0], tuple)
    assert resolver.require(CatchUpRuntime).deps.plans is resolver.require(PlanCatchUpPort)
    world = resolver.require(PlanTools).world
    assert world.guided_checks is composition
    # SYSTEM 5.12 table and ground-link-readers: only rules, catchup and mission read GroundLinkQuery, so the
    # plans ground line keeps its structural reader and never binds to the real link.
    assert world.ground_line is not None and world.ground_line.reader is None


@pytest.mark.trace("T28-24", "T28-42", "EI1", "AGENTS-3")
def test_plans_line_stays_unknown_beside_the_real_groundlink_module(composed: Composed) -> None:
    from crew_health.plans.application.release import UiCopy

    s, resolver = composed.stack, composed.resolver
    with s.db.snapshot() as view:
        assert resolver.require(GroundLinkQuery).current(view) is not None
    world = resolver.require(PlanTools).world
    assert world.ground_line is not None
    copy = UiCopy.from_release(s.release)
    assert copy is not None
    assert world.ground_line.line(copy, NOW) == (copy.text("designed_states", "unknown"), None)


def checked(run_id: str, release_id: str) -> GuidedCheck:
    return GuidedCheck(
        guided_check_id="gck_owner",
        revision=1,
        skill_id="pulse_check_wrist",
        check_id="pulse_count",
        environment_id="lunar_surface",
        helper_id="tomas",
        subject_id="mara",
        measured_record_id="mara",
        count_origin="SIM",
        plan_run_id=run_id,
        plan_step_id="icl10_pulse_by_crewmate",
        episode_id="ep_test",
        role_assignment_id=None,
        state="checked",
        teach_step_no=1,
        steps_done=[],
        attempt=None,
        helper_answer=None,
        answer_id=None,
        previous_answer_id=None,
        camera=None,
        result="no_reference",
        difference_exact=None,
        count_exact=None,
        result_text=None,
        observation_id=None,
        experimental=False,
        pins={},
        config_release_id=release_id,
        created_at=NOW,
        created_by_session="ses_mara",
    )


@pytest.mark.trace("T28-05", "T28-42", "SYSTEM-8.1", "PR-I8")
def test_registered_guided_completion_and_supply_use_share_teach_transaction(tmp_path: Path) -> None:
    s = stack(tmp_path)
    scratch_supply_step(s)
    import json

    from .plans_stack import obj, rows

    raw = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    steps = [v for stage in rows(rows(raw["plans"])[0]["stages"]) for v in rows(stage.get("steps", []))]
    pulse = next(v for v in steps if v["step_id"] == "icl10_pulse_by_crewmate")
    pulse["needs"] = next(v for v in steps if v["step_id"] == "icl10_note_episodes")["needs"]
    s.replace_document(ConfigName.ONBOARD_PLANS, raw)
    composed = compose(s)
    run = s.start()
    run_id = str(run["plan_run_id"])
    subscriptions = composed.registry.declarations.get("subscriptions", {})
    for name, declaration in subscriptions.items():
        event_type, handler = cast(
            tuple[type[DomainEvent], Callable[[UnitOfWork, DomainEvent], None]], declaration
        )
        s.db.bus.subscribe(event_type, handler, name=str(name))
    event = teach.GuidedCheckCompleted("gck_owner", run_id, "icl10_pulse_by_crewmate", "no_reference", ())
    with s.db.begin(actor=s.session.actor, correlation_id="cor_owner_finish") as uow:
        SqliteGuidedChecks().add(uow, checked(run_id, s.release.release_id))
        uow.publish(event)
    current = s.runs.get(s.session, run_id)
    assert run_step(current, "icl10_pulse_by_crewmate")["state"] == "done"
    assert isinstance(run_step(current, "icl10_pulse_by_crewmate")["supply_use_ids"], list)
    assert len(cast(list[str], run_step(current, "icl10_pulse_by_crewmate")["supply_use_ids"])) == 1
    with s.db.begin(actor=s.session.actor, correlation_id="cor_owner_duplicate") as uow:
        uow.publish(event)
    assert s.runs.get(s.session, run_id) == current
    assert s.query("SELECT count(*) FROM plans_test_debit") == ((1,),)


@pytest.mark.trace("T28-42", "AGENTS-3", "SYSTEM-5.12")
def test_owner_modules_keep_their_absent_plans_fallbacks(tmp_path: Path) -> None:
    from crew_health.kitmap.application.errors import KitMapUnavailable
    from crew_health.kitmap.application.ports import KitMapQueries
    from crew_health.kitmap.application.queries import KitMapReads
    from crew_health.measurement.application.ports import MeasurementCommands
    from crew_health.measurement.module import _Commands

    composed = compose(stack(tmp_path), plans=False)
    resolver = composed.resolver
    kitmap = resolver.require(KitMapQueries)
    assert isinstance(kitmap, KitMapReads) and kitmap.plans is None
    measurement = resolver.require(MeasurementCommands)
    assert isinstance(measurement, _Commands)
    assert measurement._live.require_service()._prov.gap_plans is None
    assert resolver.require(CatchUpRuntime).deps.plans is None
    with composed.stack.db.snapshot() as view:
        ctx = KitMapContext(
            view, cast(KitMapRelease, ReleaseView(view.release.release_id)), "lunar_surface", NOW
        )
        with pytest.raises(KitMapUnavailable):
            kitmap.columns(ctx, frozenset(), ("plan_icl10",))
        with pytest.raises(KitMapUnavailable):
            resolver.require(PlanColumnsPort).row_columns(ctx, kit=frozenset(), plan_ids=("plan_icl10",))
        with pytest.raises(KitMapUnavailable):
            resolver.require(PlanColumnsPort).plans_changed(
                ctx, base_kit=frozenset(), delta=PlanningDelta((), (), ())
            )
    assert (
        resolver.require(GapPlanPort).plan_for(
            condition_ids=("ICL10",), environment_id="lunar_surface", subject_person_id="mara"
        )
        is None
    )


@pytest.mark.trace("T28-27", "T28-42", "SYSTEM-5.12")
@pytest.mark.parametrize("requirement", ["instrument", "supply"])
def test_owner_adapter_translates_nonempty_planning_delta(tmp_path: Path, requirement: str) -> None:
    from crew_health.kernel.units import Quantity, Unit
    from crew_health.kitmap.application.ports import PlanChange, SkillAddition, SupplyAddition

    from .test_plans_provided import scratch_plan

    s = stack(tmp_path)
    scratch_plan(s, requirement)
    composed = compose(s)
    port = composed.resolver.require(PlanColumnsPort)
    delta = PlanningDelta(
        ("scratch_instrument",) if requirement == "instrument" else (),
        (SupplyAddition("eye_rinse_water", Quantity(Decimal(1), Unit("{rinse}"))),)
        if requirement == "supply"
        else (),
        (SkillAddition("pulse_check_wrist", ("tomas",)),),
    )
    with s.db.snapshot() as view:
        ctx = KitMapContext(
            view, cast(KitMapRelease, ReleaseView(view.release.release_id)), "lunar_surface", NOW
        )
        changes = port.plans_changed(ctx, base_kit=frozenset(), delta=delta)
    assert len(changes) == 1 and isinstance(changes[0], PlanChange)
    assert not changes[0].before_plan_aboard and changes[0].after_plan_aboard
    assert s.events == []


@pytest.mark.trace("T28-42", "SYSTEM-8.1")
def test_catchup_owner_shape_preserves_replacement_quotes(composed: Composed) -> None:
    from .plans_stack import obj, rows

    s = composed.stack
    run = s.start(plan_id="plan_eye_injury")
    provided = composed.resolver.require(PlanCatchUpPort)
    inputs = provided.catch_up_inputs(str(run["plan_run_id"]))
    assert isinstance(inputs, PlanCatchUpInputs)
    content = obj(json.loads(s.docs[ConfigName.ONBOARD_PLANS].raw))
    eye = next(plan for plan in rows(content["plans"]) if plan["plan_id"] == "plan_eye_injury")
    quotes = tuple(
        str(obj(step["replaces_ground_task"])["quote"])
        for stage in rows(eye["stages"])
        for step in rows(stage.get("steps", []))
        if step["replaces_ground_task"] is not None
    )
    assert quotes and inputs.replaces_ground_task == quotes


@pytest.mark.trace("T28-42", "SYSTEM-8.1")
def test_unpublished_help_and_time_ports_keep_the_system_signatures() -> None:
    import inspect
    from typing import get_type_hints

    from crew_health.kernel.uow import ReadSnapshot
    from crew_health.plans.application.ports import HelpEscalationReceipt, PlanEscalation, PlanHelpEntry

    assert tuple(inspect.signature(PlanHelpEntry.open_from_plan_step).parameters) == ("self", "request")
    assert get_type_hints(PlanHelpEntry.open_from_plan_step) == {
        "request": PlanEscalation,
        "return": HelpEscalationReceipt,
    }
    assert tuple(inspect.signature(PlanHelpEntry.receipt).parameters) == (
        "self",
        "snapshot",
        "session",
        "escalation_id",
    )
    assert get_type_hints(PlanHelpEntry.receipt) == {
        "snapshot": ReadSnapshot,
        "session": SessionContext,
        "escalation_id": str,
        "return": HelpEscalationReceipt | None,
    }
    assert tuple(inspect.signature(InTimeWords.render).parameters) == ("self", "now", "until")
