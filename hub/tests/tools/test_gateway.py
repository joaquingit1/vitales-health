from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from crew_health.clock.settings import specifications as clock_specs
from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import ClockCorrection, ClockSegment, TimeInput
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.testing import (
    MemoryClock,
    MemoryClocks,
    MemoryIds,
    MemoryRelease,
    MemorySettings,
    MemoryStorage,
    MemoryUow,
    MemoryUowFactory,
)
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.platform.settings import specifications as platform_specs
from crew_health.tools.adapters.jsonschema_validator import PolicyValidator
from crew_health.tools.adapters.registry import StaticToolRegistry
from crew_health.tools.application.gateway import Gateway
from crew_health.tools.application.ports import (
    HandlerNoResult,
    HandlerOk,
    HandlerOutcome,
    HandlerRejected,
    HandlerUnavailable,
)
from crew_health.tools.domain.arguments import JsonBounds
from crew_health.tools.domain.phase import Phase
from crew_health.tools.domain.scope import ToolScope
from crew_health.tools.domain.tool_result import ToolCall
from crew_health.tools.settings import setting_specs
from tests.tools.test_bounds import settings_values


class View(MemoryUow):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    actor = Actor("person", "person_a")
    correlation_id = "cor_a"

    def __init__(self) -> None:
        specs = (*platform_specs(), *clock_specs())
        settings = MemorySettings(
            specs, {spec.key: int(spec.maximum) for spec in specs if spec.maximum is not None}
        )
        raw = json.dumps(
            json.loads(
                Path("contracts/api/examples/config/assistant-policy.example.json").read_text(
                    encoding="utf-8"
                )
            )["value"]
        ).encode("utf-8")
        release = MemoryRelease(
            "cfg_a",
            {
                ConfigName.ASSISTANT_POLICY: ConfigDocumentView(
                    ConfigName.ASSISTANT_POLICY, hashlib.sha256(raw).hexdigest(), raw
                )
            },
        )
        clock = MemoryClock(type(self).now, 0, "epoch_a")
        factory = MemoryUowFactory(
            clock=clock,
            ids=MemoryIds(identifiers=tuple(str(i) for i in range(100))),
            release=release,
            settings=settings,
            connection=MemoryStorage(settings=settings, responses={}, results={}),
            node_id="node_a",
            incarnation="inc_a",
        )
        super().__init__(factory, type(self).actor, type(self).correlation_id)
        self.fail_audit = False
        clocks = MemoryClocks(settings=settings, clock=clock, node_id="node_a", incarnation="inc_a")
        clocks.open(self, ClockSegment("clock_a", "physical_a", "segment_a", None, 1, 1))
        clocks.apply(
            self,
            ClockCorrection(
                "correction_a",
                "clock_a",
                "segment_a",
                1,
                type(self).now - timedelta(days=2),
                None,
                0,
                0,
                type(self).now,
                "operator",
            ),
        )
        self.state.audit.clear()

    @property
    def audits(self) -> list[AuditEntry]:
        return self.state.audit

    def audit(self, entry: AuditEntry) -> None:
        from crew_health.kernel.errors import StorageUnavailable

        if self.fail_audit:
            raise StorageUnavailable("disk_full")
        super().audit(entry)


class Authority:
    live = True

    def require_current(self, uow: object, scope: ToolScope) -> bool:
        return self.live

    def authorize_arguments(self, uow: object, scope: ToolScope, name: str, arguments: object) -> bool:
        return self.live

    def authorize_result(self, uow: object, scope: ToolScope, name: str, data: object) -> bool:
        return self.live


class Handler:
    name = "get_capabilities"
    effect: Literal["read", "compute", "proposal"] = "read"

    def __init__(self) -> None:
        self.calls = 0
        self.outcome: HandlerOutcome = HandlerOk(b'{"items":[]}', (), None, ())

    def handle(self, uow: object, scope: ToolScope, arguments: object, deadline: object) -> HandlerOutcome:
        self.calls += 1
        return self.outcome


def scope() -> ToolScope:
    session = SessionContext("ses_a", "crew", View.actor, "person_a", frozenset())
    deadline = ElapsedDeadline(
        TimeInput(View.now, "clock_a", "segment_a", 1, "hub_corrected", "LIVE"), timedelta(seconds=1)
    )
    return ToolScope(
        session,
        "person_a",
        "mission_a",
        "LIVE",
        None,
        None,
        None,
        "turn_a",
        "conv_a",
        "cfg_a",
        "evr_a",
        "model_a",
        Phase.INTAKE,
        1,
        1,
        deadline,
    )


def clock_of(gate: Gateway) -> MemoryClock:
    return cast(MemoryClock, gate._clock)


def gateway() -> tuple[Gateway, Handler, Authority]:
    settings = MemorySettings(setting_specs(), settings_values())
    handler = Handler()
    registry = StaticToolRegistry(settings)
    registry.register("get_capabilities", handler)
    registry.freeze()
    authority = Authority()
    from crew_health.tools.adapters.jsonschema_validator import CatalogValidator

    catalog = CatalogValidator(settings).compile(
        b'{"tools":[{"name":"get_capabilities","stage":"intake","effect":"read","input_schema":{"type":"object","properties":{"codes":{"type":"array","items":{"type":"string","maxLength":32},"maxItems":3}},"required":["codes"],"additionalProperties":false}}]}',
        model_release_id="model_a",
    )
    parsed = PolicyValidator(JsonBounds(262144, 16, 50, 2000)).parse(
        View().release.document(ConfigName.ASSISTANT_POLICY).raw
    )
    clock = MemoryClock(View.now, 0, "epoch_a")
    return (
        Gateway(
            registry,
            lambda model_release_id: catalog,
            settings,
            authority,
            clock,
            MemoryIds(identifiers=tuple(str(i) for i in range(50))),
            policy=lambda uow: parsed,
            corrections=MemoryClocks(
                settings=View().factory.settings,
                clock=MemoryClock(View.now, 0, "epoch_a"),
                node_id="node_a",
                incarnation="inc_a",
            ),
            capture=lambda uow, anchor: TimeInput(
                clock.now(), anchor.source_clock_id, anchor.segment_id, 1, "hub_corrected", "LIVE"
            ),
        ),
        handler,
        authority,
    )


@pytest.mark.trace("T-E12-001", "T-E12-003", "T-E12-004", "R17", "V25", "I1", "I2")
@pytest.mark.parametrize(
    ("call", "changed", "reason"),
    [
        (ToolCall("call_a", "delete_file", b"{}"), {}, "unknown_tool"),
        (
            ToolCall("call_a", "get_capabilities", b'{"codes":[]}'),
            {"phase": Phase.ASSESSMENT},
            "out_of_stage",
        ),
        (
            ToolCall("call_a", "get_capabilities", b'{"codes":[],"person_id":"foreign"}'),
            {},
            "malformed_schema",
        ),
        (ToolCall("call_a", "get_capabilities", b'{"codes":[]}'), {"subject_person_id": "person_b"}, "scope"),
    ],
)
def test_denial_precedes_handler_and_audits_safe_reason(
    call: ToolCall, changed: dict[str, Any], reason: str
) -> None:
    gate, handler, _ = gateway()
    view = View()
    result = gate.execute(view, replace(scope(), **changed), call)
    assert result.status == "denied"
    assert result.error_code == "MODEL_OUTPUT_REJECTED"
    assert handler.calls == 0
    assert len(view.audits) == 1
    assert reason in str(view.audits)
    assert "foreign" not in str(view.audits)
    assert "delete_file" not in str(view.audits)


@pytest.mark.trace("T-E12-004", "T-E12-005", "I2")
def test_invalid_calls_consume_budget_and_execution_is_not_cached() -> None:
    gate, handler, _ = gateway()
    view = View()
    call = ToolCall("call_a", "get_capabilities", b'{"codes":[]}')
    for _ in range(8):
        assert gate.execute(view, scope(), call).status == "ok"
    assert gate.execute(view, scope(), call).status == "denied"
    assert handler.calls == 8
    assert len(view.audits) == 9


@pytest.mark.trace("T-E12-004", "I5")
def test_audit_fault_propagates_and_revocation_refuses_execution() -> None:
    from crew_health.kernel.errors import StorageUnavailable

    gate, handler, authority = gateway()
    view = View()
    authority.live = False
    assert (
        gate.execute(view, scope(), ToolCall("call_a", "get_capabilities", b'{"codes":[]}')).status
        == "denied"
    )
    assert handler.calls == 0
    authority.live = True
    view.fail_audit = True
    with pytest.raises(StorageUnavailable):
        gate.execute(view, scope(), ToolCall("call_a", "get_capabilities", b'{"codes":[]}'))


@pytest.mark.trace("T-E12-004", "T-E12-014", "I5")
def test_real_sqlite_without_turn_audit_binding_fails_closed(tmp_path: Path) -> None:
    from crew_health.kernel.errors import ProblemError
    from crew_health.platform.testing.sqlite import database

    gate, handler, _ = gateway()
    db = database(tmp_path)
    with pytest.raises(ProblemError):
        with db.begin(actor=View.actor, correlation_id="cor_a") as uow:
            gate.execute(
                uow, replace(scope(), config_release_id="release_1"), ToolCall("call_a", "delete_file", b"{}")
            )
    assert handler.calls == 0
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM platform_audit", (), limit=1) == ((0,),)


@pytest.mark.trace("T-E12-004", "T-E12-005", "T-E12-020", "I2", "I3")
@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (HandlerNoResult(()), "no_result"),
        (HandlerUnavailable(()), "unavailable"),
        (HandlerRejected("policy"), "denied"),
        (HandlerOk(b'{"value":NaN}', (), None, ()), "denied"),
        (HandlerOk(b'"' + b"x" * 2001 + b'"', (), None, ()), "denied"),
    ],
)
def test_result_validation_preserves_missing_states_and_rejects_bad_values(
    outcome: HandlerOutcome, status: str
) -> None:
    gate, handler, _ = gateway()
    handler.outcome = outcome
    view = View()
    result = gate.execute(view, scope(), ToolCall("call_a", "get_capabilities", b'{"codes":[]}'))
    assert result.status == status
    assert len(view.audits) == 1


@pytest.mark.trace("T-E12-005", "T-E12-013", "T-E12-014", "I2", "I6")
@pytest.mark.parametrize("fault", ["generation", "concurrency", "unbound", "effect", "timeout", "revoked"])
def test_turn_and_handler_faults_cannot_publish_accepted_result(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    gate, handler, authority = gateway()
    target = scope()
    call = ToolCall("call_a", "get_capabilities", b'{"codes":[]}')
    if fault == "generation":
        assert gate.execute(View(), target, call).status == "ok"
        target = replace(target, generation=2)
    elif fault == "concurrency":
        assert gate._lock.acquire(blocking=False)
    elif fault == "unbound":
        monkeypatch.setattr(gate._registry, "handler", lambda name: None)
    elif fault == "effect":
        monkeypatch.setattr(handler, "effect", "proposal")
    elif fault in {"timeout", "revoked"}:

        def changed(*args: object) -> HandlerOutcome:
            if fault == "timeout":
                clock_of(gate).monotonic += 1000000001
            else:
                authority.live = False
            return HandlerOk(b"{}", (), None, ())

        monkeypatch.setattr(handler, "handle", changed)
    result = gate.execute(View(), target, call)
    assert result.status == ("unavailable" if fault == "unbound" else "denied")


@pytest.mark.trace("T-E12-020", "I3")
def test_result_metadata_times_and_status_are_validated() -> None:
    from crew_health.tools.domain.errors import ToolsInvalid
    from crew_health.tools.domain.tool_result import ToolResult

    result = ToolResult("call_a", "ok", b"{}", ("obs_a",), View.now, View.now, (), None)
    assert result.provenance_ids == ("obs_a",)
    with pytest.raises(ToolsInvalid):
        replace(result, status="denied")


@pytest.mark.trace("T-E12-020", "T-E12-051", "R17", "I2", "I3")
def test_wrong_tool_failed_numeric_and_warning_payload_is_rejected() -> None:
    gate, handler, _ = gateway()
    handler.outcome = HandlerOk(
        b'{"quality":"rejected","quantity":{"value":123,"unit":"1"},"level":"emergency","action":"clear"}',
        (),
        None,
        (),
    )
    result = gate.execute(View(), scope(), ToolCall("call_a", "get_capabilities", b'{"codes":[]}'))
    assert result.status == "denied"
    assert result.data is None


@pytest.mark.trace("T-E12-011", "I7")
@pytest.mark.parametrize("binding", ["policy", "corrections", "capture"])
def test_missing_mandatory_binding_is_unavailable_before_dispatch(binding: str) -> None:
    gate, handler, _ = gateway()
    setattr(gate, "_" + binding, None)
    result = gate.execute(View(), scope(), ToolCall("call_a", "get_capabilities", b'{"codes":[]}'))
    assert result.status == "unavailable"
    assert result.error_code == "CAPABILITY_UNAVAILABLE"
    assert handler.calls == 0


@pytest.mark.trace("T-E12-005", "T-E12-014", "I2")
def test_an_anchor_older_than_the_turn_allowance_never_runs_the_handler() -> None:
    """Review r1 finding 4: the reviewer moved the one-second anchor back one day and the handler still ran."""
    gate, handler, _ = gateway()
    target = scope()
    old = replace(
        target.deadline, anchor=replace(target.deadline.anchor, original_at=View.now - timedelta(days=1))
    )
    view = View()
    result = gate.execute(
        view, replace(target, deadline=old), ToolCall("call_a", "get_capabilities", b'{"codes":[]}')
    )
    assert result.status == "denied"
    assert handler.calls == 0
    assert "tool_timeout" in str(view.audits)


@pytest.mark.trace("T-E12-005", "I2")
def test_calls_accumulate_against_the_turn_and_a_later_call_sees_the_time_already_spent() -> None:
    gate, handler, _ = gateway()
    view = View()
    call = ToolCall("call_a", "get_capabilities", b'{"codes":[]}')
    assert gate.execute(view, scope(), call).status == "ok"
    clock_of(gate).at = View.now + timedelta(seconds=2)
    assert gate.execute(view, scope(), call).status == "denied"
    assert handler.calls == 1


@pytest.mark.trace("T-E12-004", "T-E12-005", "I2")
def test_turns_of_different_conversations_keep_separate_budgets_and_never_stale_each_other() -> None:
    gate, handler, _ = gateway()
    view = View()
    call = ToolCall("call_a", "get_capabilities", b'{"codes":[]}')
    for _ in range(8):
        assert gate.execute(view, scope(), call).status == "ok"
    other = replace(scope(), turn_id="turn_b", conversation_id="conv_b")
    assert gate.execute(view, other, call).status == "ok"
    assert gate.execute(view, scope(), call).status == "denied"
    assert handler.calls == 9


@pytest.mark.trace("T-E12-005", "I2")
def test_a_full_turn_table_refuses_a_new_turn_until_an_open_turn_expires() -> None:
    gate, _, _ = gateway()
    gate._max_turns = 1
    view = View()
    call = ToolCall("call_a", "get_capabilities", b'{"codes":[]}')
    assert gate.execute(view, scope(), call).status == "ok"
    later = replace(scope(), turn_id="turn_b")
    assert gate.execute(view, later, call).status == "denied"
    clock_of(gate).at = View.now + timedelta(seconds=2)
    moved = replace(
        later.deadline, anchor=replace(later.deadline.anchor, original_at=View.now + timedelta(seconds=2))
    )
    assert gate.execute(view, replace(later, deadline=moved), call).status == "ok"


@pytest.mark.trace("T-E12-005", "I2")
def test_the_policy_pinned_in_the_callers_release_is_read_on_every_call() -> None:
    gate, handler, _ = gateway()
    seen: list[object] = []
    pinned = gate._policy

    def policy(uow: object) -> object:
        seen.append(uow)
        return None if len(seen) > 1 else pinned(uow)  # type: ignore[arg-type, misc]

    gate._policy = policy  # type: ignore[assignment]
    call = ToolCall("call_a", "get_capabilities", b'{"codes":[]}')
    first, second = View(), View()
    assert gate.execute(first, scope(), call).status == "ok"
    assert gate.execute(second, scope(), call).status == "unavailable"
    assert seen == [first, second]
    assert handler.calls == 1


@pytest.mark.trace("T-E12-011", "I7")
def test_a_scope_pinned_to_another_model_release_finds_no_catalog() -> None:
    gate, handler, _ = gateway()
    result = gate.execute(
        View(),
        replace(scope(), model_release_id="model_b"),
        ToolCall("call_a", "get_capabilities", b'{"codes":[]}'),
    )
    assert result.status == "unavailable"
    assert handler.calls == 0
