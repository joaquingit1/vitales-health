"""Real platform and guidance composition for the E28 application acceptance cases."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, TypedDict, Unpack, cast

import jsonschema
from referencing import Registry, Resource

from crew_health.app.registry import DeclarationRegistry
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.guidance.application.facade import EligibilityFacade, FactProviders, GuidanceBounds
from crew_health.guidance.application.ports import (
    ContentFactSet,
    ContentRef,
    Fact,
    GuidanceEligibility,
    Known,
    OperatorFact,
    Requirements,
    SkillFacts,
    StockFact,
    Unknown,
)
from crew_health.help.application.ports import HelpContext, HelpFlowRef, HelpSessionSnapshot
from crew_health.kernel.commands import AcceptedReceipt, canonical_json
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.testing import MemoryClock, MemoryRelease, MemorySettings
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import (
    Actor,
    OutboxEvent,
    ReadSnapshot,
    SessionContext,
    StoredEvent,
    UnitOfWork,
)
from crew_health.plans.adapters.repository import SqlitePlanRuns
from crew_health.plans.application.act import ActOnPlanRun
from crew_health.plans.application.consumers import PlanConsumers
from crew_health.plans.application.ports import (
    EpisodeReader,
    EpisodeRef,
    HelpEscalationReceipt,
    ObservationRef,
    ObservationRefReader,
    PlanEscalation,
    PlanHelpEntry,
    StockLevel,
    SupplyNeed,
)
from crew_health.plans.application.release import PlansCatalog
from crew_health.plans.application.runs import PlanRuns
from crew_health.plans.application.settings import PlansSettings
from crew_health.plans.application.teach_copies import GuidedCheckFacts
from crew_health.plans.application.world import PlansWorld
from crew_health.plans.domain.model import canonical_sha256
from crew_health.platform.adapters.intents import IntentRepository
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.testing.sqlite import database
from crew_health.roles.application.ports import MissionFacts, RosterMember, RosterPort

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 14, 13, 37, tzinfo=UTC)
PLANS_VALUES = {
    "plans.max_plans_per_document": 300,
    "plans.max_steps_per_plan": 40,
    "plans.max_observations_per_record": 16,
    "plans.max_rechecks_per_step": 500,
    "plans.max_escalations_per_run": 64,
    "plans.max_text_chars": 2000,
    "plans.parsed_plan_cache_entries": 512,
    "plans.page_limit_max": 200,
    "plans.max_document_bytes": 4194304,
}


def obj(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def rows(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [obj(v) for v in value]


def load_json(path: Path) -> dict[str, object]:
    raw = obj(json.loads(path.read_text(encoding="utf-8")))
    return obj(raw["value"]) if "value" in raw else raw


def example_documents() -> dict[ConfigName, ConfigDocumentView]:
    names = (
        ConfigName.ONBOARD_PLANS,
        ConfigName.SKILLS_CATALOG,
        ConfigName.UI_COPY,
        ConfigName.ENVIRONMENT_PROFILES,
        ConfigName.ROUTINE_RULES,
        ConfigName.ROLE_RULES,
    )
    raw = {
        name: load_json(ROOT / "docs/product/api/examples/config" / (name.value + ".example.json"))
        for name in names
    }
    raw[ConfigName.KIT_MAP] = load_json(ROOT / "contracts/api/examples/kit-map.example.json")
    raw[ConfigName.RESPONDER_FLOWS] = load_json(ROOT / "docs/product/responder-flows.json")
    return {name: document(name, value) for name, value in raw.items()}


def document(name: ConfigName, value: Mapping[str, object]) -> ConfigDocumentView:
    raw = canonical_json(value)
    return ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)


def session(person_id: str = "mara", *, officer: bool = False) -> SessionContext:
    roles = {Role.CREW}
    if officer:
        roles.add(Role.CREW_MEDICAL_OFFICER)
    return SessionContext("ses_" + person_id, "crew", Actor("person", person_id), person_id, frozenset(roles))


@dataclass
class Mission:
    environment: str | None = "lunar_surface"
    mode: Literal["LIVE", "REPLAY", "SIM"] = "SIM"

    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None:
        if self.environment is None:
            return None
        return MissionFacts("mission_test", self.mode, self.environment, None, "node_test")

    def day_end(self, instant: datetime) -> datetime:
        return instant.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


class CrewFacts:
    def __init__(self) -> None:
        data = rows(load_json(ROOT / "contracts/api/examples/crew/list.json")["items"])
        self.members = tuple(
            RosterMember(
                str(r["person_id"]), str(r["display_name"]), i, cast(int | None, r["medical_officer_rank"])
            )
            for i, r in enumerate(data)
        )
        self.skills: dict[str, Literal["fresh", "due", "stale"]] = {"pulse_check_wrist": "fresh"}

    def crew(self, at: datetime) -> Sequence[RosterMember]:
        return self.members

    def operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> Fact[tuple[OperatorFact, ...]]:
        return Known(
            tuple(
                OperatorFact(
                    m.person_id, m.roster_index, True, True, False, m.medical_officer_rank, self.skills
                )
                for m in self.members
            )
        )


class Content:
    """Validates the request like a real E10 provider: the step must exist in the named release with that hash."""

    def __init__(self) -> None:
        self.unknown = False
        self.releases: dict[str, MemoryRelease] = {}

    def content(
        self, snapshot: ReadSnapshot, content: ContentRef, *, purpose: str, release_id: str
    ) -> ContentFactSet:
        release = self.releases.get(release_id)
        steps: dict[str, tuple[dict[str, object], dict[str, object]]] = {}
        if release is not None:
            doc = obj(json.loads(release.document(ConfigName.ONBOARD_PLANS).raw))
            for plan in rows(doc["plans"]):
                for stage in rows(plan["stages"]):
                    for step in rows(stage.get("steps", stage.get("prepare", []))):
                        steps[str(step["step_id"])] = (plan, step)
        found = steps.get(content.id)
        hash_valid = found is not None and canonical_sha256(found[1]) == content.content_sha256
        environments: frozenset[str] = frozenset()
        substitute: tuple[ContentRef, Requirements] | None = None
        if found is not None:
            plan, step = found
            environments = frozenset(cast(list[str], step.get("environments") or plan["environments"]))
            substitute = self.substitute(steps, step, content)
        source: Fact[bool] = (
            Unknown("content", "unverifiable") if self.unknown or release is None else Known(hash_valid)
        )
        return ContentFactSet(
            source,
            Known(True),
            "not_clinically_reviewed",
            Known(False),
            Known(False),
            release is not None,
            Known(environments),
            None if substitute is None else substitute[0],
            None if substitute is None else substitute[1],
        )

    @staticmethod
    def substitute(
        steps: Mapping[str, tuple[dict[str, object], dict[str, object]]],
        step: Mapping[str, object],
        content: ContentRef,
    ) -> tuple[ContentRef, Requirements] | None:
        """The configured equipment substitute, as E10 resolves it from the same pinned document."""
        found = steps.get(str(step.get("substitute_step_id")))
        if found is None:
            return None
        sub = found[1]
        needs = obj(sub["needs"])
        if needs["supplies"] or sub["who"] != "any_crew":
            return None  # This fake resolves only the shapes the plans tests use.
        return ContentRef(content.kind, str(sub["step_id"]), canonical_sha256(sub)), Requirements(
            frozenset(cast(list[str], needs["instruments"])),
            {},
            frozenset(cast(list[str], needs["skills"])),
            "any_crew",
            None,
        )


class EnvironmentFacts:
    def __init__(self, mission: Mission) -> None:
        self.mission = mission

    def applicable(
        self, snapshot: ReadSnapshot, patient_location_id: str | None, environments: Collection[str]
    ) -> Fact[bool]:
        return Known(self.mission.environment in environments)


class EquipmentFacts:
    def __init__(self, mission: Mission) -> None:
        self.mission = mission
        self.removed: set[str] = set()

    def instruments_here(
        self, snapshot: ReadSnapshot, patient_location_id: str | None
    ) -> Fact[frozenset[str]]:
        kitmap = obj(json.loads(snapshot.release.document(ConfigName.KIT_MAP).raw))
        kits = obj(kitmap["kits"])
        value = kits.get(self.mission.environment or "")
        if value is None:
            return Unknown("equipment", "no_environment_kit")
        return Known(frozenset(cast(list[str], value)) - self.removed)


@dataclass
class Episodes:
    subjects: dict[str, str] = field(default_factory=lambda: {"ep_test": "mara", "ep_other": "tomas"})

    def visible(self, session: SessionContext, episode_id: str) -> EpisodeRef | None:
        subject = self.subjects.get(episode_id)
        return (
            EpisodeRef(episode_id, subject) if subject is not None and session.person_id == subject else None
        )


@dataclass
class HelpSessions:
    subjects: dict[str, str] = field(default_factory=lambda: {"hlp_test": "mara", "hlp_escalated": "mara"})

    def context(self, session: SessionContext, help_session_id: str) -> HelpContext:
        return HelpContext(self.readable_by(session, help_session_id), "SIM", "lunar_surface")

    def readable_by(self, session: SessionContext, help_session_id: str) -> bool:
        return self.subjects.get(help_session_id) == session.person_id

    def snapshot(self, help_session_id: str) -> HelpSessionSnapshot | None:
        subject = self.subjects.get(help_session_id)
        return (
            HelpSessionSnapshot(
                help_session_id, 1, True, subject, subject, frozenset(), None, "ui_test", None
            )
            if subject is not None
            else None
        )

    def qualifying_sessions(self) -> Sequence[HelpFlowRef]:
        return ()


@dataclass
class GuidedChecks:
    checks: dict[str, GuidedCheckFacts] = field(default_factory=dict)

    def get(self, view: ReadSnapshot | UnitOfWork, guided_check_id: str) -> GuidedCheckFacts | None:
        return self.checks.get(guided_check_id)


@dataclass
class Observations:
    records: dict[str, ObservationRef] = field(default_factory=dict)

    def get_many(self, observation_ids: Sequence[str]) -> Mapping[str, ObservationRef]:
        return {key: self.records[key] for key in observation_ids if key in self.records}


class Supplies:
    """The debits live in the same real database transaction as the plan run."""

    def __init__(self) -> None:
        self.levels: dict[str, StockLevel] = {}
        self.refuse = False

    def stock(self, environment_id: str) -> Mapping[str, StockLevel]:
        return self.levels

    def uses_after_one(
        self, environment_id: str, needs: Sequence[SupplyNeed], stock: Mapping[str, StockLevel]
    ) -> Mapping[tuple[str, str], int | None]:
        return {(n.step_id, n.supply_id): 1 for n in needs}

    def record_step_uses(
        self,
        uow: UnitOfWork,
        *,
        environment_id: str,
        plan_run_id: str,
        step_id: str,
        occurrence: int,
        pinned_release_id: str,
        uses: Sequence[SupplyNeed],
        actor: Actor,
        at: datetime,
    ) -> Sequence[str]:
        ids = []
        for need in uses:
            sid = f"step:{plan_run_id}:{step_id}:{occurrence}:{need.supply_id}"
            uow.connection.execute("INSERT INTO plans_test_debit VALUES (?,?)", (sid, need.supply_id))
            if self.refuse:
                raise ProblemError(ErrorCode.SUPPLY_BELOW_ZERO, "supply depleted")
            ids.append("use_" + hashlib.sha256(sid.encode()).hexdigest()[:24])
        return tuple(ids)


class GuidanceStock:
    def __init__(self, supplies: Supplies) -> None:
        self.supplies = supplies

    def stock(
        self, snapshot: ReadSnapshot, patient_location_id: str | None, supplies: Mapping[str, Quantity | None]
    ) -> Mapping[str, Fact[StockFact]]:
        return {
            sid: Known(StockFact(level.aboard, level.quantity_now, amount, True))
            for sid, amount in supplies.items()
            if (level := self.supplies.levels.get(sid)) is not None
        }


class HelpEntry:
    def __init__(self) -> None:
        self.calls: list[PlanEscalation] = []
        self.receipts: dict[str, HelpEscalationReceipt] = {}
        self.fail = False

    def open_from_plan_step(self, request: PlanEscalation) -> HelpEscalationReceipt:
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("help unavailable")
        if request.escalation_id not in self.receipts:
            command = request.help_command
            accepted = AcceptedReceipt(
                command.key,
                command.operation,
                command.target,
                "receipt_help",
                request.client_time.original_at,
            )
            self.receipts[request.escalation_id] = HelpEscalationReceipt(
                request.escalation_id, "hlp_escalated", 1, request.pinned_release_id, accepted
            )
        return self.receipts[request.escalation_id]

    def receipt(
        self, snapshot: ReadSnapshot, session: SessionContext, escalation_id: str
    ) -> HelpEscalationReceipt | None:
        return self.receipts.get(escalation_id)


class OptionalProviders(TypedDict, total=False):
    episodes: EpisodeReader | None
    observations: ObservationRefReader | None
    help_entry: PlanHelpEntry | None
    guidance: GuidanceEligibility | None
    skills: SkillFacts | None
    roster: RosterPort | None


class PlansStack:
    def __init__(self, tmp_path: Path) -> None:
        self.clock = MemoryClock(NOW, 0, "epoch_plans")
        self.db = database(tmp_path, clock=self.clock)
        self.docs = example_documents()
        self.release = MemoryRelease("release_plans", self.docs)
        self.released = 0
        self.db.set_release_provider(lambda: self.release)
        sql = (ROOT / "hub/src/crew_health/plans/adapters/migrations/0001_plans.sql").read_text(
            encoding="utf-8"
        )
        self.db.migrate_tracked("plans.0001", hashlib.sha256(sql.encode()).hexdigest(), sql)
        sql = "CREATE TABLE plans_test_event(seq INTEGER PRIMARY KEY, payload TEXT NOT NULL) STRICT; CREATE TABLE plans_test_debit(id TEXT PRIMARY KEY, supply_id TEXT NOT NULL) STRICT;"
        self.db.migrate_tracked("plans.test", hashlib.sha256(sql.encode()).hexdigest(), sql)
        for event in ("plan_run.changed", "plan_run.step_done", "plan_run.escalated"):
            self.db.register_audit_policy(
                AuditPolicy(event, {}, frozenset({"plan_run_id"}), {"plan_run_id": frozenset({"plan_run"})})
            )
        schemas = ROOT / "contracts/api/schemas"
        registry: Registry = Registry().with_resources(
            (p.name, Resource.from_contents(json.loads(p.read_text(encoding="utf-8"))))
            for p in schemas.glob("*.schema.json")
        )
        self.validator = jsonschema.Draft202012Validator(
            json.loads((schemas / "event.schema.json").read_text(encoding="utf-8")), registry=registry
        )
        self.db.event_sink = self.record_event
        settings = PlansSettings.read(MemorySettings(PlansSettings.specs(), PLANS_VALUES))
        self.mission, self.crew, self.content = Mission(), CrewFacts(), Content()
        self.content.releases[self.release.release_id] = self.release
        self.equipment = EquipmentFacts(self.mission)
        self.supplies, self.episodes, self.help_sessions = Supplies(), Episodes(), HelpSessions()
        self.guided, self.observations, self.help_entry = GuidedChecks(), Observations(), HelpEntry()
        guidance = EligibilityFacade(GuidanceBounds(200, 32))
        guidance.attach(
            FactProviders(
                self.content,
                EnvironmentFacts(self.mission),
                self.equipment,
                self.crew,
                GuidanceStock(self.supplies),
            )
        )
        ledger = CommandLedger(self.db.ids, settings=self.db.settings)
        self.world = PlansWorld(
            uow=self.db,
            snapshots=self.db,
            clock=self.clock,
            ids=self.db.ids,
            settings=settings,
            idempotency=ledger,
            intents=IntentRepository(ledger),
            repository=SqlitePlanRuns(settings),
            catalog=PlansCatalog(settings),
            guidance=guidance,
            skills=self.crew,
            roster=self.crew,
            mission=self.mission,
            episodes=self.episodes,
            help_sessions=self.help_sessions,
            stock=self.supplies,
            cover=self.supplies,
            supply_ledger=self.supplies,
            help_entry=self.help_entry,
            guided_checks=self.guided,
            observations=self.observations,
        )
        self.runs, self.actions = PlanRuns(self.world), ActOnPlanRun(self.world)
        self.consumers = PlanConsumers(self.world)
        self.session = session()

    def bind_plan_subscriptions(self) -> None:
        """Install the production guided completion path: teach's subscription forwards its real event to the
        sink the plans module registers (teach/module.py PlansComposition)."""
        from crew_health.plans.application.teach_copies import GuidedCompletionSink
        from crew_health.plans.module import PlansRuntime, Resolver, register
        from crew_health.teach.application.ports import GuidedCheckCompleted as TeachCompleted
        from crew_health.teach.application.ports import GuidedCheckQueries as TeachQueries
        from crew_health.teach.module import PlansComposition

        registry = DeclarationRegistry(max_ports=64, max_hooks=128)
        register(registry)
        factory, _ = registry.providers[GuidedCompletionSink]
        runtime = cast(PlansRuntime, factory(cast(Resolver, object())))
        runtime.world = self.world
        composition = PlansComposition(cast(TeachQueries, self.guided))
        composition.sink = runtime
        self.db.bus.subscribe(TeachCompleted, composition.completed, name="teach.plans_guided_completion")

    def record_event(self, uow: UnitOfWork, event: OutboxEvent) -> StoredEvent:
        count = uow.connection.rows("SELECT count(*) FROM plans_test_event", (), limit=1)
        seq = int(str(count[0][0])) + 1
        event_id = f"event_{seq}"
        payload = {
            "seq": seq,
            "event_id": event_id,
            "type": event.type.value,
            "occurred_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
            "emitted_at": uow.now.isoformat().replace("+00:00", "Z"),
            "scope": {
                "kind": event.scope.kind,
                "person_id": event.scope.person_id,
                "environment_id": event.scope.environment_id,
            },
            "config_release_id": event.config_release_id,
            "correlation_id": event.correlation_id,
            "data": json.loads(event.data_json),
        }
        self.validator.validate(payload)
        assert event.audience.rule == "plans.run_viewers"
        assert event.audience.object_ref == obj(payload["data"])["plan_run_id"]
        uow.connection.execute("INSERT INTO plans_test_event VALUES (?,?)", (seq, json.dumps(payload)))
        return StoredEvent(
            seq,
            event_id,
            event.type,
            event.occurred_at,
            uow.now,
            event.scope,
            event.config_release_id,
            event.correlation_id,
            event.data_json,
            event.audience,
        )

    @property
    def events(self) -> list[dict[str, object]]:
        with self.db.snapshot() as view:
            data = view.connection.rows("SELECT payload FROM plans_test_event ORDER BY seq", (), limit=1000)
        return [obj(json.loads(str(row[0]))) for row in data]

    def query(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> tuple[tuple[SqlValue, ...], ...]:
        with self.db.snapshot() as view:
            return view.connection.rows(sql, parameters, limit=1000)

    def replace_document(self, name: ConfigName, value: Mapping[str, object]) -> None:
        self.docs[name] = document(name, value)
        self.released += 1
        self.release = MemoryRelease(f"release_changed_{self.released}", self.docs)
        self.content.releases[self.release.release_id] = self.release

    def skip_ids(self, count: int) -> None:
        """Advance the deterministic ids, for a station reopened over a file another process wrote."""
        for _ in range(count):
            self.db.ids.new("skip")

    def rebind(self, **changes: Unpack[OptionalProviders]) -> None:
        self.world = replace(self.world, **changes)
        self.runs, self.actions = PlanRuns(self.world), ActOnPlanRun(self.world)
        self.consumers = PlanConsumers(self.world)

    def start(
        self,
        *,
        key: str = "start",
        plan_id: str = "plan_icl10",
        episode_id: str | None = "ep_test",
        help_session_id: str | None = None,
        person: SessionContext | None = None,
    ) -> dict[str, object]:
        selected = person or self.session
        status, run = self.runs.start(
            selected,
            {
                "plan_id": plan_id,
                "subject_person_id": selected.person_id,
                "episode_id": episode_id,
                "help_session_id": help_session_id,
            },
            key,
        )
        assert status == 201
        return run

    def act(
        self,
        run: Mapping[str, object],
        action: str,
        *,
        step_id: str | None = None,
        key: str | None = None,
        if_match: int | None = None,
        **extra: object,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "action": action,
            "client_time": NOW.isoformat().replace("+00:00", "Z"),
            **extra,
        }
        if step_id is not None:
            body["step_id"] = step_id
        revision = cast(int, run["revision"]) if if_match is None else if_match
        return self.actions.act(
            self.session, str(run["plan_run_id"]), body, key or action + "_" + (step_id or "run"), revision
        )


def stack(tmp_path: Path) -> PlansStack:
    return PlansStack(tmp_path)


def run_step(run: Mapping[str, object], step_id: str) -> dict[str, object]:
    return next(s for s in rows(run["steps"]) if s["step_id"] == step_id)
