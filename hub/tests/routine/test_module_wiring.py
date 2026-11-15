"""Routine declarations composed with the actual core providers on this branch."""

# mypy: disable-error-code="type-abstract"
from __future__ import annotations

import inspect
from pathlib import Path
from typing import cast

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from crew_health.app.assembly import BuildCatalog
from crew_health.app.registry import DeclarationRegistry
from crew_health.kernel.errors import ProblemError
from crew_health.routine.application import ports
from crew_health.routine.application.ports import RequestAuthenticator, RoutineQueries
from crew_health.routine.module import RoutineRuntime, register
from crew_health.teach.application.ports import RequestAuthenticator as TeachAuthenticator

from .http_harness import HttpHarness, headers, problem
from .support import REPO


@pytest.mark.trace("T-E25-015", "T-E25-096")
def test_real_core_then_routine_has_no_conflicting_declarations_and_freezes() -> None:
    registry = DeclarationRegistry(max_ports=256, max_hooks=1024)
    for registration in BuildCatalog.core(REPO / "hub/src/crew_health").registrations:
        registration(registry)
    register(registry)
    routers = tuple(cast(APIRouter, r) for r in registry.declarations["routers"].values())
    operations = {
        route.operation_id for router in routers for route in router.routes if isinstance(route, APIRoute)
    }
    assert {
        "getRoutineToday",
        "getRoutineWeek",
        "listOccurrences",
        "getOccurrence",
        "actOnOccurrence",
        "listRoutineTasks",
        "getRoutineTask",
        "listOverrides",
        "createOverride",
        "actOnOverride",
    } <= operations
    assert len(registry.declarations["settings"]) > 12
    assert {"ro", "rov", "rq"} <= registry.declarations["prefixes"].keys()
    assert {"routine.occurrence", "routine.override"} <= registry.declarations["audiences"].keys()
    assert "routine.release" in registry.declarations["loader_checks"]
    assert {
        "routine.day",
        "routine.occurrence",
        "routine.override",
        "routine.instance",
    } <= registry.declarations["scheduler_handlers"].keys()
    assert "routine.inputs" in registry.declarations["durable_subscribers"]
    registry.freeze(required_loader_checks=frozenset(str(k) for k in registry.declarations["loader_checks"]))
    assert registry.frozen


@pytest.mark.trace("T-E25-015", "INV-E25-16")
def test_unbound_routes_fail_closed_and_bound_routes_without_authenticator_reject_all_calls(
    tmp_path: Path,
) -> None:
    runtime = RoutineRuntime()
    registry = DeclarationRegistry(max_ports=64, max_hooks=256)
    register(registry)
    assert RoutineQueries in registry.providers
    app = FastAPI()
    for router in registry.declarations["routers"].values():
        app.include_router(cast(APIRouter, router))
    problem(TestClient(app).get("/v1/routine/today", headers=headers()), "INTERNAL_ERROR", 500)
    hub = HttpHarness(tmp_path)
    hub.runtime.authenticator = None
    for route in hub.router.routes:
        if not isinstance(route, APIRoute):
            continue
        path = (
            route.path.replace("{occurrence_id}", "ro_absent")
            .replace("{item_id}", "missing")
            .replace("{override_id}", "rov_absent")
        )
        response = hub.client.request(
            "POST" if "POST" in (route.methods or set()) else "GET",
            path,
            headers=headers(),
            json={} if "POST" in (route.methods or set()) else None,
        )
        problem(response, "UNAUTHENTICATED", 401)
    with pytest.raises(ProblemError):
        runtime.active_release_id()


@pytest.mark.trace("T-E25-015", "T-E25-096")
def test_authenticator_matches_teach_and_records_copy_is_removed() -> None:
    assert inspect.signature(RequestAuthenticator.authenticate, eval_str=True) == inspect.signature(
        TeachAuthenticator.authenticate, eval_str=True
    )
    assert not hasattr(ports, "AnswerAccepted")
