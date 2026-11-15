"""Bounded owner calls, typed error mapping and agent guards (SYSTEM 3, CONTRACTS 12)."""

import threading
from pathlib import Path
from typing import Any, cast

import pytest

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.groundlink.adapters.audience import GroundLinkAudience
from crew_health.groundlink.api.errors import to_problem
from crew_health.groundlink.application.agent import AgentPass
from crew_health.groundlink.application.owner import OwnerCalls
from crew_health.groundlink.application.ports import InboundItem, OutboxItem
from crew_health.groundlink.domain.errors import BundleConflict, DemoSwitchInvalid, GroundLinkError
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.uow import Actor, Audience, AudienceProjectionKey, ReadSnapshot, SessionContext

from .support import Harness


@pytest.mark.trace("R48", "E29-01")
def test_transport_errors_map_to_typed_problems_without_text() -> None:
    invalid = to_problem(DemoSwitchInvalid("next_contact_at"))
    assert invalid.code == ErrorCode.INVALID_VALUE
    assert [f.field for f in invalid.field_errors] == ["next_contact_at"]
    assert to_problem(BundleConflict("private")).code == ErrorCode.INVALID_TRANSITION
    other = to_problem(GroundLinkError("private"))
    assert other.code == ErrorCode.INTERNAL_ERROR and other.retryable is True
    assert "private" not in str(other)


@pytest.mark.trace("R48", "EI5")
def test_one_outstanding_owner_call_reports_busy_then_its_own_result() -> None:
    calls = OwnerCalls(timeout_ms=10)
    release = threading.Event()

    def slow() -> str:
        release.wait(2)
        return "applied"

    assert calls.call("evt_1", slow) == "owner_timeout"
    assert calls.call("evt_2", lambda: "applied") == "owner_busy"
    release.set()
    assert calls.pending is not None and calls.pending.done.wait(2)
    assert calls.call("evt_1", lambda: "never runs") is None
    assert calls.pending is None


@pytest.mark.trace("R48", "EI5")
def test_a_finished_call_for_another_key_is_dropped_and_the_new_call_runs() -> None:
    calls = OwnerCalls(timeout_ms=10)
    release = threading.Event()
    assert calls.call("evt_1", lambda: "applied" if release.wait(2) else "late") == "owner_timeout"
    release.set()
    assert calls.pending is not None and calls.pending.done.wait(2)
    assert calls.call("evt_2", lambda: "duplicate") is None


@pytest.mark.trace("R48", "EI5")
def test_owner_outcomes_outside_the_port_contract_are_invalid() -> None:
    assert OwnerCalls(timeout_ms=1000).call("evt_1", lambda: "maybe") == "owner_invalid_outcome"


@pytest.mark.trace("R48", "EI5")
def test_storage_failures_in_the_owner_are_raised_now_or_on_the_next_call() -> None:
    def broken() -> str:
        raise StorageUnavailable("disk_full")

    with pytest.raises(StorageUnavailable):
        OwnerCalls(timeout_ms=1000).call("evt_1", broken)
    calls = OwnerCalls(timeout_ms=10)
    release = threading.Event()

    def late() -> str:
        release.wait(2)
        raise StorageUnavailable("locked")

    assert calls.call("evt_1", late) == "owner_timeout"
    release.set()
    assert calls.pending is not None and calls.pending.done.wait(2)
    with pytest.raises(StorageUnavailable):
        calls.call("evt_2", lambda: "applied")
    assert calls.pending is None


@pytest.mark.trace("R48", "E29-01")
def test_agent_pass_is_skipped_while_another_runs_or_the_environment_is_unknown(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    agent = h.runtime.agent
    assert agent.lock.acquire(blocking=False)
    try:
        assert agent.run_once() == AgentPass()
    finally:
        agent.lock.release()
    unknown = Harness(tmp_path / "unknown", mission=False)
    assert unknown.runtime.agent.run_once() == AgentPass()


@pytest.mark.trace("R48", "E29-01")
def test_link_events_reach_crew_ground_and_operator_sessions_only() -> None:
    audience = GroundLinkAudience()
    rule = Audience(
        "groundlink.link", "ground-link", AudienceProjectionKey("groundlink", "ground_link", 1), "1"
    )
    other = Audience("help.crew", "ground-link", AudienceProjectionKey("groundlink", "ground_link", 1), "1")
    view = cast(ReadSnapshot, None)
    for kind, admitted in (("crew", True), ("ground", True), ("operator", True), ("station", False)):
        session = SessionContext(
            "session_1", cast(Any, kind), Actor("operator", "operator_1"), None, frozenset()
        )
        assert audience.admits(view, session, rule) is admitted
        assert audience.admits(view, session, other) is False


@pytest.mark.trace("R48", "R61", "E29-04")
def test_outbox_and_inbox_refuse_malformed_items_with_field_paths(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    with pytest.raises(ProblemError) as bad_hash:
        with h.db.begin(actor=Actor("operator", "operator_1"), correlation_id="cor_1") as uow:
            h.outbox.enqueue(uow, OutboxItem("ground_message", "msg_1", "A" * 64, h.clock.at, True))
    assert [f.field for f in bad_hash.value.field_errors] == ["/content_sha256"]
    with h.db.snapshot() as view, pytest.raises(ProblemError) as bad_limit:
        h.outbox.delivered(view, since=None, limit=0)
    assert [f.field for f in bad_limit.value.field_errors] == ["/limit"]
    receipt = InboundItem(
        "evt_r1", "ground_message", "msg_1", "receipt", h.clock.at, "text", Actor("operator", "operator_1")
    )
    with pytest.raises(ProblemError) as bad_text:
        h.inbox.receive(receipt)
    assert [f.field for f in bad_text.value.field_errors] == ["/text"]
