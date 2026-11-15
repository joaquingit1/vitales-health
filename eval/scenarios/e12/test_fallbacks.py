"""E12 fallback scenarios (docs/build/E12/PLAN.md, test_fallbacks.py): with the model, its catalog, the
evidence owner, the measurement owner and the shared text rules absent, every E12 port still resolves through
the real registry, fails closed with CAPABILITY_UNAVAILABLE and writes nothing, so the hub's fixed safety
paths keep running without the assistant (T-E12-070, AGENTS rule 3)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from crew_health.app.registry import DeclarationRegistry
from crew_health.kernel.clock import Clock
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemoryClock, MemoryIds, MemoryRelease
from crew_health.tools.application.ports import (
    ContextBuilderPort,
    EffectApplier,
    OutputPolicyPort,
    TextField,
    ToolGateway,
    TurnData,
)
from crew_health.tools.domain.context import ContextBudget
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.tool_result import ToolCall
from crew_health.tools.module import register
from e12_stack import settings
from tests.tools.test_effects import proposal
from tests.tools.test_gateway import View, scope

PROPOSAL = {"capability_code": "pulse", "reason": "Palpitations", "decision_affected": "Review today"}


def resolved() -> object:
    registry = DeclarationRegistry(max_ports=64, max_hooks=200)
    hub = settings()
    registry.provide(HubSettings, lambda _: hub)  # type: ignore[type-abstract]
    registry.provide(Clock, lambda _: MemoryClock(datetime(2026, 1, 1, tzinfo=UTC), 0, "epoch_a"))  # type: ignore[type-abstract]
    registry.provide(IdFactory, lambda _: MemoryIds(identifiers=tuple(str(i) for i in range(50))))  # type: ignore[type-abstract]
    register(registry)
    return registry.resolve()


@pytest.mark.trace("T-E12-070", "T-E12-011", "I7")
@pytest.mark.parametrize(
    "call",
    [
        ToolCall("call_a", "get_capabilities", b'{"codes":[]}'),
        ToolCall("call_a", "read_source_passage", b'{"passage_id":"psg_a","include_parent":false}'),
        ToolCall(
            "call_a",
            "propose_measurement",
            b'{"capability_code":"pulse","reason":"r","decision_affected":"d"}',
        ),
    ],
    ids=["capabilities", "passage", "proposal"],
)
def test_without_owners_every_tool_call_fails_closed_and_runs_no_handler(call: ToolCall) -> None:
    gateway = resolved().require(ToolGateway)  # type: ignore[attr-defined]
    view = View()
    result = gateway.execute(view, scope(), call)
    assert (result.status, result.error_code, result.data) == ("unavailable", "CAPABILITY_UNAVAILABLE", None)
    assert [(entry.event_type, entry.safe_metadata["status"]) for entry in view.audits] == [
        ("tools.executed", "unavailable")
    ]


@pytest.mark.trace("T-E12-070", "T-E12-060", "I7")
def test_without_shared_text_rules_no_model_text_is_ever_allowed() -> None:
    policy = resolved().require(OutputPolicyPort)  # type: ignore[attr-defined]
    field = TextField("text", "Take two tablets now", "step_a")
    verdict = policy.check((field,), scope(), TurnData(MemoryRelease("cfg_a", {}), 1, (field,)))
    assert (verdict.allowed, verdict.error_code) == (False, "CAPABILITY_UNAVAILABLE")


@pytest.mark.trace("T-E12-070", "T-E12-014", "I5")
def test_without_identity_or_the_measurement_owner_an_effect_is_refused_and_nothing_is_written() -> None:
    """The scope check runs first: with no live E02 session nothing reaches even the owner lookup."""
    effects = resolved().require(EffectApplier)  # type: ignore[attr-defined]
    view = View()
    with pytest.raises(ToolsInvalid) as raised:
        effects.apply(view, scope(), (proposal(),), "turn_a")
    assert raised.value.code == "MODEL_OUTPUT_REJECTED"
    assert view.audits == []


@pytest.mark.trace("T-E12-070", "T-E12-040", "I7")
def test_without_owner_projections_no_model_context_is_built() -> None:
    builder = resolved().require(ContextBuilderPort)  # type: ignore[attr-defined]
    with pytest.raises(ToolsInvalid) as raised:
        builder.build(View(), scope(), scope().phase, ContextBudget(32768, 0, ("observations",)))
    assert raised.value.code == "CAPABILITY_UNAVAILABLE"
