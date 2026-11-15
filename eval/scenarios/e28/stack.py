"""E05 fallback: real plans routers, SQLite, activation and registered E27 guidance.

This is not create_app acceptance. E26's HelpEntryPort has no keyed plan-entry
method, E31 requires absent overlays/day labels, E30 requires absent record
routing, and E27 kitmap requires absent TemplateRenderer/condition providers.
The plans test harness supplies only platform fixtures and typed SIM boundaries.
Other configuration contributors are explicitly SIM, not release certification.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from httpx import Response

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from crew_health.app.registry import DeclarationRegistry, Resolver  # noqa: E402
from crew_health.config.adapters.repository import ConfigRepository, SQLiteConfigReader  # noqa: E402
from crew_health.config.application.activation import ReleaseActivationService  # noqa: E402
from crew_health.config.application.loader import ConfigLoader, LoaderChecks, required_contributors  # noqa: E402
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role  # noqa: E402
from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope  # noqa: E402
from crew_health.contracts_gen.schemas.plan_run_schema import PlanRun  # noqa: E402
from crew_health.guidance.application.ports import (  # noqa: E402
    ContentFacts,
    Known,
    OperatorFact,
    SkillFacts,
    StockFacts,
)
from crew_health.guidance.application.ports import (  # noqa: E402
    EnvironmentFacts as EnvironmentPort,
)
from crew_health.guidance.application.ports import (  # noqa: E402
    EquipmentFacts as EquipmentPort,
)
from crew_health.guidance.module import register as guidance_register  # noqa: E402
from crew_health.guidance.module import setting_specs  # noqa: E402
from crew_health.help.application.ports import HelpSessionReader  # noqa: E402
from crew_health.kernel.clock import Clock  # noqa: E402
from crew_health.kernel.commands import CommandIntents, IdempotencyStore  # noqa: E402
from crew_health.kernel.config import CandidateRelease, Finding  # noqa: E402
from crew_health.kernel.errors import ProblemError  # noqa: E402
from crew_health.kernel.ids import IdFactory  # noqa: E402
from crew_health.kernel.settings import HubSettings  # noqa: E402
from crew_health.kernel.testing import MemoryRelease, MemorySettings  # noqa: E402
from crew_health.kernel.uow import (  # noqa: E402
    Actor,
    OutboxEvent,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    StoredEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.plans.api.router import RequestAuthenticator  # noqa: E402
from crew_health.plans.application.assistant_copies import (  # noqa: E402
    AnswerScope,
    ToolHandler,
    ToolRegistry,
)
from crew_health.plans.application.ground_log import (  # noqa: E402
    GroundLinkReader,
    InTimeWords,
    LinkSnapshot,
)
from crew_health.plans.application.ports import (  # noqa: E402
    EpisodeReader,
    HelpEscalationReceipt,
    PlanEscalation,
    PlanHelpEntry,
    StockReader,
    SupplyCoverPort,
    SupplyLedgerPort,
    SupplyNeed,
)
from crew_health.plans.application.release_check import PlansReleaseCheck  # noqa: E402
from crew_health.plans.application.settings import PlansSettings  # noqa: E402
from crew_health.plans.module import PlansRuntime, register  # noqa: E402
from crew_health.platform.domain.audit import AuditPolicy  # noqa: E402
from crew_health.platform.storage import SQLiteDatabase  # noqa: E402
from crew_health.roles.application.ports import MissionStatePort, PlanRunReader, RosterPort  # noqa: E402

from hub.tests.plans.plans_stack import (  # noqa: E402
    NOW,
    PLANS_VALUES,
    Content,
    CrewFacts,
    EnvironmentFacts,
    Episodes,
    EquipmentFacts,
    GuidanceStock,
    HelpEntry,
    HelpSessions,
    Mission,
    PlansStack,
    Supplies,
    document,
    load_json,
    obj,
    rows,
    run_step,
    session,
)

__all__ = [
    "NOW",
    "ROOT",
    "Station",
    "checked",
    "document",
    "load_json",
    "obj",
    "refused",
    "rows",
    "run_step",
    "steps",
    "view_step",
]


class SimContentFacts(Content):
    """E10 owner: SIM stand in for source/review eligibility, not clinical approval."""


class SimEnvironmentFacts(EnvironmentFacts):
    """E23 owner: SIM stand in for current environment and movement facts."""


class SimEquipmentFacts(EquipmentFacts):
    """E27 kitmap boundary: SIM stand in while its required providers are absent."""


class SimRoster(CrewFacts):
    """E02/E31 owners: SIM stand in for roster, availability and practiced skills."""

    def __init__(self) -> None:
        super().__init__()
        self.absent: set[str] = set()

    def operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> Known[tuple[OperatorFact, ...]]:
        return Known(
            tuple(
                OperatorFact(
                    m.person_id,
                    m.roster_index,
                    m.person_id not in self.absent,
                    m.person_id not in self.absent,
                    False,
                    m.medical_officer_rank,
                    self.skills,
                )
                for m in self.members
            )
        )


class SimMission(Mission):
    """E02/E23 owners: SIM stand in for mission mode and environment."""


class SimEpisodes(Episodes):
    """E15 owner: SIM stand in for episode ownership."""


class SimHelpSessions(HelpSessions):
    """E26 owner: SIM stand in because keyed E28 entry is not exported."""


class SimHelpEntry(HelpEntry):
    """E26 owner: SIM stand in for durable keyed help entry; no real E26 claim."""

    def __init__(self, sessions: SimHelpSessions) -> None:
        super().__init__()
        self.sessions = sessions

    def open_from_plan_step(self, request: PlanEscalation) -> HelpEscalationReceipt:
        result = super().open_from_plan_step(request)
        # Distinct occurrences get distinct sessions after the preceding session closed.
        if request.occurrence > 1:
            result = replace(result, help_session_id="hlp_occurrence_" + str(request.occurrence))
            self.receipts[request.escalation_id] = result
        # The session the SIM entry opened is readable through the SIM help reader, as E26's would be.
        self.sessions.subjects[result.help_session_id] = request.subject_person_id
        return result


class SimGroundLinkReader:
    """E29 owner: SIM stand in for link telemetry."""

    status: Literal["up", "delayed", "down"] = "down"

    def current(self) -> LinkSnapshot:
        return LinkSnapshot(self.status, NOW + timedelta(hours=25), None, True, NOW)


class SimInTimeWords:
    """E29 owner: SIM stand in for relative mission time wording."""

    def render(self, now: datetime, until: datetime) -> str:
        return str(int((until - now).total_seconds() // 3600)) + " hours"


class SimReleaseContributor:
    """E01 and uncomposed configuration owners: SIM stand in for non-E28 checks."""

    def __init__(self, check_id: str) -> None:
        self.check_id = check_id

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        return ()


class SimSessions:
    """E02 owner: SIM stand in for authenticated crew, officer, station and ground sessions."""

    def session(self, request: Request, operation_id: str) -> SessionContext:
        selected = request.headers.get("X-Test-Person", "mara")
        if selected in {"station", "ground"}:
            return SessionContext(
                "ses_" + selected,
                "station" if selected == "station" else "ground",
                Actor("person", selected),
                None,
                frozenset({Role.STATION if selected == "station" else Role.GROUND}),
            )
        return session(selected, officer=selected == "tomas")


class SimStockFacts(GuidanceStock):
    """E32 owner: SIM stand in for guidance stock facts."""


class SimToolRegistry:
    """E12 owner: SIM stand in for handler registration, without gateway/output policy."""

    def __init__(self) -> None:
        self.handlers: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        assert name not in self.handlers
        self.handlers[name] = handler

    def handler(self, name: str) -> ToolHandler | None:
        return self.handlers.get(name)


class SimAnswerScope:
    """E04/E26 owners: SIM stand in for answer membership in the controller's context."""

    def answer_belongs(
        self, uow: UnitOfWork, answer_id: str, episode_id: str | None, help_session_id: str | None
    ) -> bool:
        return answer_id == "answer_here" and episode_id == "ep_test" and help_session_id is None


class SimSupplyLedger(Supplies):
    """E32 owner: SIM stand in with debit bounds enforced in the real transaction."""

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
        for need in uses:
            level = self.levels.get(need.supply_id)
            used = uow.connection.rows(
                "SELECT count(*) FROM plans_test_debit WHERE supply_id=?", (need.supply_id,), limit=1
            )[0][0]
            if level is None or level.quantity_now is None or int(str(used)) >= level.quantity_now.value:
                raise ProblemError(ErrorCode.SUPPLY_BELOW_ZERO, "SIM stock exhausted")
        return super().record_step_uses(
            uow,
            environment_id=environment_id,
            plan_run_id=plan_run_id,
            step_id=step_id,
            occurrence=occurrence,
            pinned_release_id=pinned_release_id,
            uses=uses,
            actor=actor,
            at=at,
        )


def provide(registry: DeclarationRegistry, port: type[object], value: object) -> None:
    def factory(resolver: Resolver) -> object:
        return value

    registry.provide(port, factory)


def shipped_documents() -> dict[ConfigName, object]:
    result: dict[ConfigName, object] = {}
    for name in ConfigName:
        path = ROOT / "config" / (name.value + ".json")
        if not path.exists():
            path = ROOT / "docs/product/api/examples/config" / (name.value + ".example.json")
        if not path.exists():
            path = ROOT / "docs/product" / (name.value + ".json")
        result[name] = load_json(path)
    return result


class Station:
    """E05 fallback with real registration, HTTP and activation, named SIM counterparts."""

    def __init__(self, tmp_path: Path) -> None:
        self.base = PlansStack(tmp_path)
        self.db = self.base.db
        self.mission = SimMission()
        self.crew = SimRoster()
        self.content = SimContentFacts()
        self.equipment = SimEquipmentFacts(self.mission)
        self.supplies = SimSupplyLedger()
        self.help_sessions = SimHelpSessions()
        self.help = SimHelpEntry(self.help_sessions)
        self.link = SimGroundLinkReader()
        self.tools = SimToolRegistry()
        self.config = ConfigRepository()
        self.editor = replace(session("editor"), roles=frozenset({Role.EDITOR}))
        self.db.migrate(
            (ROOT / "hub/src/crew_health/config/adapters/migrations/0001_config.sql").read_text(
                encoding="utf-8"
            )
        )
        self.db.event_sink = self.record_event
        self.documents = {name: document(name, obj(raw)) for name, raw in shipped_documents().items()}
        self.check = PlansReleaseCheck(self.base.world.settings, lambda: self.mission.mode)
        checks = LoaderChecks(maximum=128)
        for identifier in sorted(required_contributors()):
            checks.register(SimReleaseContributor(identifier))
        checks.register(self.check)
        self.activation = ReleaseActivationService(
            self.config,
            ConfigLoader(checks, max_document_bytes=8388608),
            self.db.ids,
            origin=lambda view: self.mission.mode,
        )
        self.install("release_e28_shipped", {})
        self.db.set_release_reader(lambda view: SQLiteConfigReader(view, self.config).active())
        self.bind()

    def record_event(self, uow: UnitOfWork, event: OutboxEvent) -> StoredEvent:
        if event.type.value.startswith("plan_run."):
            return self.base.record_event(uow, event)
        return StoredEvent(
            0,
            "sim_config_event",
            event.type,
            event.occurred_at,
            uow.now,
            event.scope,
            event.config_release_id,
            event.correlation_id,
            event.data_json,
            event.audience,
        )

    def install(self, identifier: str, changes: Mapping[ConfigName, Mapping[str, object]]) -> None:
        candidate = dict(self.documents)
        candidate.update({name: document(name, raw) for name, raw in changes.items()})
        with self.db.begin(actor=self.editor.actor, correlation_id="propose_" + identifier) as uow:
            result = self.activation.propose(uow, self.editor, CandidateRelease(identifier, candidate))
        assert result.passed, result.errors
        with self.db.begin(actor=self.editor.actor, correlation_id="activate_" + identifier) as uow:
            revision = self.activation.revision(uow, identifier)
            self.activation.activate(uow, self.editor, release_id=identifier, expected_etag=revision.etag)
        self.documents = candidate
        # The SIM content provider checks each step hash against the release it is asked about (E10 rule).
        self.content.releases[identifier] = MemoryRelease(identifier, candidate)

    def raw(self, name: ConfigName) -> dict[str, object]:
        return obj(json.loads(self.documents[name].raw))

    def bind(self) -> None:
        self.registry = DeclarationRegistry(max_ports=128, max_hooks=256)
        register(self.registry)
        guidance_register(self.registry)
        settings = MemorySettings(
            (*PlansSettings.specs(), *setting_specs()),
            {**PLANS_VALUES, "guidance.max_requirement_items": 200, "guidance.max_operators": 32},
        )
        for port, value in (
            (UnitOfWorkFactory, self.db),
            (ReadSnapshotFactory, self.db),
            (Clock, self.db.clock),
            (IdFactory, self.db.ids),
            (HubSettings, settings),
            (IdempotencyStore, self.base.world.idempotency),
            (CommandIntents, self.base.world.intents),
            (RosterPort, self.crew),
            (MissionStatePort, self.mission),
            (SkillFacts, self.crew),
            (ContentFacts, self.content),
            (EnvironmentPort, SimEnvironmentFacts(self.mission)),
            (EquipmentPort, self.equipment),
            (StockFacts, SimStockFacts(self.supplies)),
            (HelpSessionReader, self.help_sessions),
            (EpisodeReader, SimEpisodes()),
            (PlanHelpEntry, self.help),
            (StockReader, self.supplies),
            (SupplyCoverPort, self.supplies),
            (SupplyLedgerPort, self.supplies),
            (RequestAuthenticator, SimSessions()),
            (GroundLinkReader, self.link),
            (InTimeWords, SimInTimeWords()),
            (ToolRegistry, self.tools),
            (AnswerScope, SimAnswerScope()),
        ):
            provide(self.registry, port, value)
        self.resolver = self.registry.resolve()
        self.runtime = cast(PlansRuntime, self.resolver.require(cast(type[object], PlanRunReader)))
        self.app = FastAPI()
        for router in self.registry.declarations["routers"].values():
            self.app.include_router(cast(APIRouter, router))
        self.client = TestClient(self.app)

    def restart(self) -> None:
        previous = self.db
        with previous.snapshot() as snapshot:
            release = snapshot.release
        previous.close()
        self.db = SQLiteDatabase(
            previous.path,
            clock=previous.clock,
            ids=previous.ids,
            release=release,
            settings=previous.settings,
            origin_node_id=previous.origin_node_id,
            origin_incarnation=previous.origin_incarnation,
            initialize=False,
        )
        self.db.set_release_reader(lambda view: SQLiteConfigReader(view, self.config).active())
        self.tools = SimToolRegistry()
        self.base.db = self.db
        for event in ("plan_run.changed", "plan_run.step_done", "plan_run.escalated"):
            self.db.register_audit_policy(
                AuditPolicy(event, {}, frozenset({"plan_run_id"}), {"plan_run_id": frozenset({"plan_run"})})
            )
        self.db.event_sink = self.record_event
        self.bind()

    def query(self, table: str) -> tuple[tuple[object, ...], ...]:
        assert table in {
            "plans_run",
            "plans_test_event",
            "plans_test_debit",
            "platform_audit",
            "platform_command_ledger",
            "plans_run_escalation",
            "plans_escalation_intent",
        }
        with self.db.snapshot() as snapshot:
            return snapshot.connection.rows("SELECT * FROM " + table, (), limit=1000)

    def all_rows(self) -> dict[str, tuple[tuple[object, ...], ...]]:
        with self.db.snapshot() as snapshot:
            names = snapshot.connection.rows(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", (), limit=512
            )
            return {
                str(row[0]): snapshot.connection.rows(
                    'SELECT * FROM "' + str(row[0]).replace('"', '""') + '"', (), limit=1000
                )
                for row in names
            }

    def effects(self) -> tuple[tuple[tuple[object, ...], ...], ...]:
        return tuple(
            self.query(t)
            for t in (
                "plans_run",
                "plans_test_event",
                "plans_test_debit",
                "platform_audit",
                "platform_command_ledger",
            )
        )

    def start(self, key: str = "start_acceptance", plan: str = "plan_icl10") -> dict[str, object]:
        return checked(
            self.client.post(
                "/v1/plan-runs",
                json={"plan_id": plan, "subject_person_id": "mara", "episode_id": "ep_test"},
                headers={"Idempotency-Key": key},
            ),
            201,
        )

    def act(
        self,
        run: Mapping[str, object],
        action: str,
        *,
        step: str | None = None,
        key: str | None = None,
        person: str = "mara",
        **extra: object,
    ) -> Response:
        body = {"action": action, "client_time": NOW.isoformat().replace("+00:00", "Z"), **extra}
        if step is not None:
            body["step_id"] = step
        return cast(
            Response,
            self.client.post(
                "/v1/plan-runs/" + str(run["plan_run_id"]) + "/actions",
                json=body,
                headers={
                    "Idempotency-Key": key or action + "_" + (step or "run"),
                    "If-Match": '"' + str(run["revision"]) + '"',
                    "X-Test-Person": person,
                },
            ),
        )


def checked(response: Response, status: int = 200) -> dict[str, object]:
    assert response.status_code == status, response.text
    value = obj(response.json())
    PlanRun.model_validate(value)
    assert response.headers["ETag"] == '"' + str(value["revision"]) + '"'
    assert response.headers["Config-Release"]
    return value


def refused(response: Response, status: int, code: str) -> dict[str, object]:
    assert response.status_code == status, response.text
    ErrorEnvelope.model_validate(response.json())
    value = obj(response.json())
    assert value["code"] == code
    return value


def view_step(view: Mapping[str, object], identifier: str) -> dict[str, object]:
    return next(
        step
        for stage in rows(view["stages"])
        for step in rows(stage.get("steps", stage.get("prepare", [])))
        if step["step_id"] == identifier
    )


def steps(raw: Mapping[str, object], plan_id: str = "plan_icl10") -> list[dict[str, object]]:
    plan = next(p for p in rows(raw["plans"]) if p["plan_id"] == plan_id)
    return [
        step for stage in rows(plan["stages"]) for step in rows(stage.get("steps", stage.get("prepare", [])))
    ]
