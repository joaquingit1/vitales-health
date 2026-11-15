"""Fresh HTTP consumer composed through the production module registration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from crew_health.app.registry import DeclarationRegistry
from crew_health.contracts_gen.enums import Role
from crew_health.guidance.application.ports import GuidanceEligibility, SkillFacts
from crew_health.help.application.ports import HelpSessionReader
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandIntents, IdempotencyStore
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemorySettings
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, SessionContext, UnitOfWorkFactory
from crew_health.plans.api.router import RequestAuthenticator
from crew_health.plans.application.ports import (
    EpisodeReader,
    PlanHelpEntry,
    StockReader,
    SupplyCoverPort,
    SupplyLedgerPort,
)
from crew_health.plans.application.settings import PlansSettings
from crew_health.plans.module import PlansRuntime, register
from crew_health.roles.application.ports import MissionStatePort, PlanRunReader, RosterPort

from .plans_stack import PLANS_VALUES, PlansStack, session
from .test_plans_module import provide_value


class Authenticator:
    def session(self, request: Request, operation_id: str) -> SessionContext:
        selected = request.headers.get("X-Test-Person", "mara")
        if selected in ("station", "ground"):
            role = Role.STATION if selected == "station" else Role.GROUND
            return SessionContext(
                "ses_" + selected,
                "station" if selected == "station" else "ground",
                Actor("person", selected),
                None,
                frozenset({role}),
            )
        caller = session(selected, officer=selected == "tomas")
        if selected == "assistant":
            caller = replace(caller, actor=Actor("assistant", "assistant_test"))
        return caller


class HttpStack:
    def __init__(self, tmp_path: Path, extra_ports: Sequence[tuple[type[object], object]] = ()) -> None:
        self.stack = PlansStack(tmp_path)
        s = self.stack
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
            (RosterPort, s.crew),
            (MissionStatePort, s.mission),
            (SkillFacts, s.crew),
            (GuidanceEligibility, s.world.guidance),
            (HelpSessionReader, s.help_sessions),
            (EpisodeReader, s.episodes),
            (PlanHelpEntry, s.help_entry),
            (StockReader, s.supplies),
            (SupplyCoverPort, s.supplies),
            (SupplyLedgerPort, s.supplies),
            (RequestAuthenticator, Authenticator()),
        ):
            provide_value(registry, port, value)
        for extra_port, extra_value in extra_ports:
            provide_value(registry, extra_port, extra_value)
        resolver = registry.resolve()
        self.runtime = cast(PlansRuntime, resolver.require(cast(type[object], PlanRunReader)))
        app = FastAPI()
        for router in registry.declarations["routers"].values():
            app.include_router(cast(APIRouter, router))
        self.client = TestClient(app)

    def start(
        self, *, key: str = "open_http", person: str = "mara", plan: str = "plan_icl10"
    ) -> dict[str, object]:
        response = self.client.post(
            "/v1/plan-runs",
            json={
                "plan_id": plan,
                "subject_person_id": person,
                "episode_id": "ep_test" if person == "mara" else "ep_other",
            },
            headers={"Idempotency-Key": key, "X-Test-Person": person},
        )
        assert response.status_code == 201, response.text
        return cast(dict[str, object], response.json())
