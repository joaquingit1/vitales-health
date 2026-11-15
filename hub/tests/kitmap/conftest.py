"""Shared harness for the kit map tests: releases built from the repository's documents, and a wired hub.

Every document comes from configuration or the package (config/kit-map.json, the package example kit map, the
package ui-copy example); no clinical content is typed here.
"""

from __future__ import annotations

import functools
import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest
import yaml
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from crew_health.app.registry import DeclarationRegistry, Resolver
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.kernel.config import (
    ActivationGuard,
    CandidateRelease,
    ConfigDocumentView,
    Finding,
    ReleaseCheck,
    TemplateRenderer,
)
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import SqlValue, StatementResult
from crew_health.kernel.testing import (
    MemoryIds,
    MemoryRelease,
    MemorySettings,
    MemoryState,
    MemoryTemplateRenderer,
    MemoryView,
)
from crew_health.kernel.uow import Actor, ReadSnapshot, ReadSnapshotFactory, SessionContext, UnitOfWork
from crew_health.kitmap import module as kitmap_module
from crew_health.kitmap.api.router import RequireRoles
from crew_health.kitmap.application.ports import ConditionRegistryPort, MissionStatePort, Snap
from crew_health.kitmap.application.queries import CAN_FIELD, CANNOT_FIELD, INSTRUMENTS_PLACEHOLDER

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config/kit-map.json"
EXAMPLE = ROOT / "contracts/api/examples/kit-map.example.json"
UI_COPY_EXAMPLE = ROOT / "contracts/api/examples/config/ui-copy.example.json"
OPENAPI = ROOT / "contracts/api/openapi.yaml"
NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)

# E27 section 11 team defaults; kitmap.whatif_max_quantity and _scale follow E32's supplies.* values.
SETTINGS: dict[str, object] = {
    "kitmap.max_document_bytes": 1_048_576,
    "kitmap.max_conditions": 200,
    "kitmap.max_tests_per_row": 8,
    "kitmap.max_instruments_per_test": 8,
    "kitmap.max_instruments": 128,
    "kitmap.max_capabilities": 128,
    "kitmap.max_findings_per_row": 32,
    "kitmap.max_text_chars": 2000,
    "kitmap.max_url_chars": 2048,
    "kitmap.gate_max_condition_ids": 16,
    "kitmap.whatif_max_instruments": 64,
    "kitmap.whatif_max_supplies": 64,
    "kitmap.whatif_max_skills": 32,
    "kitmap.whatif_max_people_per_skill": 64,
    "kitmap.whatif_max_quantity": 1_000_000,
    "kitmap.whatif_max_quantity_scale": 6,
    "kitmap.parsed_release_cache": 4,
}


def example_value(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))["value"]


def ui_copy_raw(**templates: str) -> bytes:
    """The package ui-copy example, with kit_check_templates overridden only where a test edits it."""
    value = cast(dict[str, object], example_value(UI_COPY_EXAMPLE))
    current = cast(dict[str, str], value["kit_check_templates"])
    value["kit_check_templates"] = {**current, **templates}
    return json.dumps(value).encode()


def view(name: ConfigName, raw: bytes) -> ConfigDocumentView:
    return ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)


def documents(
    kit_map: bytes, *, ui_copy: bytes | None = None, onboard_plans: bytes | None = None
) -> dict[ConfigName, ConfigDocumentView]:
    docs = {ConfigName.KIT_MAP: view(ConfigName.KIT_MAP, kit_map)}
    if ui_copy is not None:
        docs[ConfigName.UI_COPY] = view(ConfigName.UI_COPY, ui_copy)
    if onboard_plans is not None:
        docs[ConfigName.ONBOARD_PLANS] = view(ConfigName.ONBOARD_PLANS, onboard_plans)
    return docs


def release(
    release_id: str, kit_map: bytes, *, ui_copy: bytes | None = None, onboard_plans: bytes | None = None
) -> MemoryRelease:
    return MemoryRelease(release_id, documents(kit_map, ui_copy=ui_copy, onboard_plans=onboard_plans))


def edited(path: Path, edit: Callable[[dict[str, object]], None]) -> bytes:
    doc = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    edit(doc)
    return json.dumps(doc).encode()


@functools.cache
def x_roles(operation_id: str) -> frozenset[str]:
    """The operation's x-roles from contracts/api/openapi.yaml (parsed once per test session)."""
    return _operation_roles()[operation_id]


@functools.cache
def _operation_roles() -> dict[str, frozenset[str]]:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return {
        operation["operationId"]: frozenset(operation.get("x-roles", ()))
        for item in spec["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict) and "operationId" in operation
    }


class SqliteSession:
    """A real sqlite StorageSession over a temporary file; the kit map must never write through it."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult:
        cursor = self.connection.execute(sql, parameters)
        return StatementResult(cursor.rowcount, cursor.lastrowid)

    def rows(
        self, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
    ) -> tuple[tuple[SqlValue, ...], ...]:
        return tuple(self.connection.execute(sql, parameters).fetchmany(limit))


@dataclass
class FakeMission:
    """MissionStatePort fake (E23 is not on main)."""

    environment_id: str | None = "station_leo"
    mode: Literal["LIVE", "REPLAY", "SIM"] | None = "LIVE"

    def mission_mode(self, uow: Snap | None = None) -> Literal["LIVE", "REPLAY", "SIM"] | None:
        return self.mode

    def active_environment_id(self, uow: Snap | None = None) -> str | None:
        return self.environment_id


SESSION_HEADER = "X-Test-Session-Role"


class FakeRequireRoles:
    """E02 require_roles fake: the role comes from a test header, the allowed set from the operation's x-roles."""

    def __call__(self, operation_id: str) -> Callable[[Request], SessionContext]:
        allowed = x_roles(operation_id)

        def dependency(request: Request) -> SessionContext:
            role = request.headers.get(SESSION_HEADER)
            if role is None:
                raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
            if role not in allowed:
                raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
            return SessionContext(
                "s_test", "crew", Actor("person", "p_test"), "p_test", frozenset({Role(role)})
            )

        return dependency


@dataclass
class Hub:
    """One registered kit map module over a sqlite file, a switchable active release and fakes for absent epics."""

    connection: sqlite3.Connection
    active: MemoryRelease
    mission: FakeMission
    registry: DeclarationRegistry
    resolver: Resolver
    client: TestClient
    now: datetime = NOW
    rejected: list[tuple[Finding, ...]] = field(default_factory=list)

    @contextmanager
    def snapshot(self) -> Iterator[ReadSnapshot]:
        yield MemoryView(
            MemoryState(), connection=SqliteSession(self.connection), release=self.active, now=self.now
        )

    def as_uow(self) -> UnitOfWork:
        return cast(
            UnitOfWork,
            MemoryView(
                MemoryState(), connection=SqliteSession(self.connection), release=self.active, now=self.now
            ),
        )

    def loader_checks(self) -> tuple[ReleaseCheck, ...]:
        return tuple(cast(Mapping[str, ReleaseCheck], self.registry.declarations["loader_checks"]).values())

    def guards(self) -> tuple[ActivationGuard, ...]:
        return tuple(
            cast(Mapping[str, ActivationGuard], self.registry.declarations["activation_guards"]).values()
        )

    def activate(self, candidate: MemoryRelease) -> bool:
        """Test activator: every loader check and guard runs; any finding rejects and the last good stays active."""
        staged = CandidateRelease(candidate.release_id, candidate.documents)
        findings = tuple(f for c in self.loader_checks() for f in c.check(staged))
        findings += tuple(f for g in self.guards() for f in g.check(self.as_uow(), staged))
        if findings:
            self.rejected.append(findings)
            return False
        self.active = candidate
        return True

    def writes(self) -> int:
        return self.connection.total_changes


def _constant(value: object) -> Callable[[Resolver], object]:
    return lambda resolver: value


class PackageConditions:
    """ConditionRegistryPort fake (E10 is not on main), read from the shipped docs/product inventory."""

    def active_ids(self) -> frozenset[str]:
        rows = json.loads((ROOT / "docs/product/condition-assessments-119.json").read_text(encoding="utf-8"))
        return frozenset(r["condition_id"] for r in rows)


class _Snapshots:
    def __init__(self, hub: Callable[[], Hub]) -> None:
        self._hub = hub

    def snapshot(self) -> AbstractContextManager[ReadSnapshot]:
        return self._hub().snapshot()


@pytest.fixture
def make_hub(tmp_path: Path) -> Iterator[Callable[..., Hub]]:
    connections: list[sqlite3.Connection] = []

    def build(
        active: MemoryRelease,
        *,
        mission: FakeMission | None = None,
        roles: bool = True,
        with_mission: bool = True,
        ports: Mapping[type[object], object] | None = None,
        registry: bool = True,
    ) -> Hub:
        connection = sqlite3.connect(tmp_path / f"hub{len(connections)}.sqlite3")
        connection.execute("CREATE TABLE canary (id INTEGER PRIMARY KEY, note TEXT NOT NULL)")
        connection.execute("INSERT INTO canary (note) VALUES ('before')")
        connection.commit()
        connections.append(connection)
        fake_mission = mission or FakeMission()
        holder: list[Hub] = []
        declarations = DeclarationRegistry(max_ports=64, max_hooks=256)
        settings = MemorySettings(kitmap_module.setting_specs(), SETTINGS)
        renderer = MemoryTemplateRenderer(
            unknown="?",
            allowed={
                CANNOT_FIELD: frozenset({INSTRUMENTS_PLACEHOLDER}),
                CAN_FIELD: frozenset({INSTRUMENTS_PLACEHOLDER}),
            },
        )
        ids = MemoryIds(identifiers=[f"{n:04d}" for n in range(512)])
        declarations.provide(HubSettings, lambda r: settings)  # type: ignore[type-abstract]
        declarations.provide(TemplateRenderer, lambda r: renderer)  # type: ignore[type-abstract]
        declarations.provide(IdFactory, lambda r: ids)  # type: ignore[type-abstract]
        declarations.provide(ReadSnapshotFactory, lambda r: _Snapshots(lambda: holder[0]))  # type: ignore[type-abstract]
        if with_mission:
            declarations.provide(MissionStatePort, lambda r: fake_mission)  # type: ignore[type-abstract]
        if roles:
            declarations.provide(RequireRoles, lambda r: FakeRequireRoles())  # type: ignore[type-abstract]
        if registry:
            declarations.provide(ConditionRegistryPort, lambda r: PackageConditions())  # type: ignore[type-abstract]
        for port, instance in (ports or {}).items():
            declarations.provide(port, _constant(instance))
        kitmap_module.register(declarations)
        resolver = declarations.resolve()
        app = FastAPI()
        for router in declarations.declarations["routers"].values():
            app.include_router(router)  # type: ignore[arg-type]
        hub = Hub(connection, active, fake_mission, declarations, resolver, TestClient(app))
        holder.append(hub)
        return hub

    yield build
    for connection in connections:
        connection.close()


@pytest.fixture
def config_raw() -> bytes:
    return CONFIG.read_bytes()


@pytest.fixture
def example_raw() -> bytes:
    return EXAMPLE.read_bytes()


@pytest.fixture
def ui_copy() -> bytes:
    return ui_copy_raw()
