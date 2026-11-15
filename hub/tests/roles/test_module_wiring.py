"""The roles module as the composition root sees it: register() mounts the routes, subscribes the CONTRACTS 18 events
that change who may help, and the dispatcher turns stored events into the handlers' effects."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import APIRouter

from crew_health.app.registry import DeclarationRegistry
from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor, Audience, AudienceProjectionKey, EventScope, StoredEvent
from crew_health.roles.module import RolesRuntime, context_events, dispatch_event, register

from .harness import Harness, assignments, help_payload, help_session, objs, person_of

ENGINE = Actor("engine", "events")
AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def _event(kind: EventType, body: dict[str, object], seq: int = 1) -> StoredEvent:
    audience = Audience("crew", "object", AudienceProjectionKey("roles", "event", 1), "policy")
    return StoredEvent(
        seq,
        f"evt_{seq}",
        kind,
        AT,
        AT,
        EventScope("crew", None, None),
        "rel_1",
        None,
        json.dumps(body).encode("utf-8"),
        audience,
    )


@pytest.mark.trace("R54")
def test_register_mounts_routes_and_subscribes_context_events() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=128)
    register(registry)
    routers = [r for r in registry.declarations["routers"].values() if isinstance(r, APIRouter)]
    paths = {getattr(route, "path", "") for router in routers for route in router.routes}
    assert {
        "/v1/help-sessions/{help_session_id}/roles",
        "/v1/plan-runs/{plan_run_id}/roles",
        "/v1/me/skills",
        "/v1/crew/readiness",
        "/v1/drills",
    } <= paths
    declared = registry.declarations["durable_subscribers"]["roles.context"]
    assert isinstance(declared, tuple)
    assert declared[0] == context_events()
    assert "roles" in registry.bindings


@pytest.mark.trace("R54")
def test_unbound_runtime_fails_loudly() -> None:
    runtime = RolesRuntime()
    with pytest.raises(ProblemError) as raised:
        runtime.my_skills(None)  # type: ignore[arg-type]
    assert raised.value.code is ErrorCode.INTERNAL_ERROR


@pytest.mark.trace("V89", "D37")
def test_stored_events_reach_the_handlers(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    # A checked guided check credits its helper: the GuidedCheck object has no occurred_at, the envelope does.
    check: dict[str, object] = {
        "guided_check_id": "gck_1",
        "skill_id": "pulse_check_wrist",
        "state": "checked",
        "result": "agree",
        "drill_id": None,
        "origin_mode": "SIM",
        "helper": {"person_id": "tomas", "display_name": "Tomas"},
    }
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        dispatch_event(h.proposals, h.skills, uow, _event(EventType.GUIDED_CHECK_CHECKED, check), check)
    skills = {s["skill_id"]: s for s in objs(h.get("/v1/me/skills", "tomas")[1]["skills"])}
    assert skills["pulse_check_wrist"]["source"] == "event"

    # Officers away, then a help session opens: the event requests a recompute and the worker proposes Tomas.
    h.facts.walk("ilya")
    h.facts.walk("sofia")
    snap = help_session("hls_wire_1", subject="mara", flow="not_breathing", opener="mara")
    h.facts.help[snap.id] = snap
    body = help_payload(snap)
    with h.uow.begin(actor=ENGINE, correlation_id="cor_t") as uow:
        dispatch_event(h.proposals, h.skills, uow, _event(EventType.HELP_SESSION_CHANGED, body, 2), body)
    assert h.proposals.run_due() == 1
    status, page = h.get(f"/v1/help-sessions/{snap.id}/roles", "mara")
    assert status == 200, page
    assert "tomas" in {person_of(row) for row in assignments(page)}
