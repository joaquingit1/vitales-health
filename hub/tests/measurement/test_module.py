"""Registration and the provided ports: CapabilityQueries, MeasurementCommands, GapProposalPort,
MeasurementAssignmentPort and the measurement_request audience rule (SYSTEM 4.2, 5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from measurement_harness import Hub, build_hub, ecg_body, headers, pulse_body, seed_crew, started_pulse

from crew_health.app.registry import DeclarationRegistry
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor, Audience
from crew_health.measurement import module
from crew_health.measurement.application.commands import PROJECTION
from crew_health.measurement.application.ports import (
    CapabilityQueries,
    GapProposalPort,
    MeasurementAssignmentPort,
    MeasurementCommands,
    ProposalInput,
)
from crew_health.measurement.application.queries import AUDIENCE_RULE


def _port(hub: Hub, port: type[Any]) -> Any:
    factory, _ = hub.registry.providers[port]
    return factory(None)  # type: ignore[arg-type]


def _proposal(body: dict[str, object], **overrides: object) -> ProposalInput:
    values: dict[str, Any] = {
        "episode_id": body.get("episode_id"),
        "capability_code": body["capability_code"],
        "reason": body["reason"],
        "decision_affected": body["decision_affected"],
        "operator_id": None,
        "body_region": None,
        "body_region_given": False,
        "condition_ids": tuple(body.get("condition_ids", ())),  # type: ignore[arg-type]
    }
    values.update(overrides)
    return ProposalInput(**values)


@pytest.mark.trace("SYSTEM-5", "T-E05-11")
def test_register_declares_bounded_settings_prefixes_and_audience() -> None:
    registry = DeclarationRegistry(max_ports=16, max_hooks=64)
    module.register(registry)
    settings = registry.declarations["settings"]
    assert {
        "measurement.page_size",
        "measurement.max_attempts",
        "measurement.replay_window_ms",
    } <= settings.keys()
    assert set(registry.declarations["prefixes"]) == {"mrq", "att", "gap", "mev"}
    assert AUDIENCE_RULE in registry.declarations["audiences"]
    assert PROJECTION in registry.declarations["local_projections"]
    for port in (CapabilityQueries, MeasurementCommands, GapProposalPort, MeasurementAssignmentPort):
        assert port in registry.providers


@pytest.mark.trace("SYSTEM-5", "I1")
def test_unbound_facades_fail_closed() -> None:
    registry = DeclarationRegistry(max_ports=16, max_hooks=64)
    module.register(registry)
    capabilities = registry.providers[CapabilityQueries][0](None)  # type: ignore[arg-type]
    with pytest.raises(ProblemError) as caught:
        capabilities.list(None)  # type: ignore[attr-defined]
    assert caught.value.code is ErrorCode.INTERNAL_ERROR and caught.value.retryable
    assignments = registry.providers[MeasurementAssignmentPort][0](None)  # type: ignore[arg-type]
    assert assignments.allows(None, request_id="mrq_1", operator_id="kai", patient_id="mara") is False  # type: ignore[attr-defined]
    rule = registry.declarations["audiences"][AUDIENCE_RULE]
    assert rule.admits(None, None, None) is False  # type: ignore[attr-defined]


@pytest.mark.trace("T-E05-15", "D06", "D30", "V23", "I1", "I2")
def test_controller_proposal_commits_in_the_callers_unit_of_work(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    commands = _port(hub, MeasurementCommands)
    with hub.uows.begin(actor=Actor("assistant", "assistant"), correlation_id="cor_turn_1") as uow:
        result = commands.propose(
            uow,
            requester=Actor("assistant", "assistant"),
            subject_person_id="mara",
            proposal=_proposal(ecg_body()),
        )
    assert result.refusal == "KIT_CANNOT_PERFORM" and result.state == "unavailable" and result.gap_id
    read = hub.client.get(f"/v1/measurement-requests/{result.request_id}", headers=headers("mara"))
    assert read.json()["requester"] == {"kind": "assistant", "id": "assistant", "display_name": None}
    with pytest.raises(RuntimeError):
        with hub.uows.begin(actor=Actor("assistant", "assistant"), correlation_id="cor_turn_2") as uow:
            commands.propose(
                uow,
                requester=Actor("assistant", "assistant"),
                subject_person_id="mara",
                proposal=_proposal(pulse_body()),
            )
            raise RuntimeError("stale turn")
    assert hub.uows.count("measurement_request") == 1


@pytest.mark.trace("T-E05-15", "T-E05-02", "R07", "I7")
def test_gap_proposal_checks_the_actual_lack_and_never_duplicates(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    gaps = _port(hub, GapProposalPort)
    episode = str(ecg_body()["episode_id"])
    engine = Actor("engine", "controller")
    with hub.uows.begin(actor=engine, correlation_id="cor_gap_1") as uow:
        first = gaps.record(uow, episode, "ecg_rhythm", str(ecg_body()["decision_affected"]))
    with hub.uows.begin(actor=engine, correlation_id="cor_gap_2") as uow:
        again = gaps.record(uow, episode, "ecg_rhythm", str(ecg_body()["decision_affected"]))
    assert first == again and hub.uows.count("measurement_gap") == 1
    with pytest.raises(ProblemError) as available:
        with hub.uows.begin(actor=engine, correlation_id="cor_gap_3") as uow:
            gaps.record(uow, episode, "pulse", "consequence")
    assert available.value.code is ErrorCode.INVALID_VALUE
    with pytest.raises(ProblemError) as unknown:
        with hub.uows.begin(actor=engine, correlation_id="cor_gap_4") as uow:
            gaps.record(uow, "epi_missing", "ecg_rhythm", "consequence")
    assert unknown.value.code is ErrorCode.UNAUTHORIZED_SCOPE
    assert hub.uows.count("measurement_gap") == 1


@pytest.mark.trace("T-E05-12", "T-E05-03", "R06", "I5")
def test_assignment_port_and_audience_rule_follow_the_object_grant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)
    request_id = str(started_pulse(hub)["request_id"])
    assignments = _port(hub, MeasurementAssignmentPort)
    rule = hub.registry.declarations["audiences"][AUDIENCE_RULE]
    audience = Audience(AUDIENCE_RULE, request_id, PROJECTION, "1")
    with hub.uows.snapshot() as snapshot:
        assert assignments.allows(snapshot, request_id=request_id, operator_id="mara", patient_id="mara")
        assert not assignments.allows(snapshot, request_id=request_id, operator_id="kai", patient_id="mara")
        assert rule.admits(snapshot, hub.sessions.sessions["mara"], audience)  # type: ignore[attr-defined]
        assert not rule.admits(snapshot, hub.sessions.sessions["kai"], audience)  # type: ignore[attr-defined]
        assert not rule.admits(snapshot, hub.sessions.sessions["cmo"], audience)  # type: ignore[attr-defined]
        assert not rule.admits(snapshot, hub.sessions.sessions["station"], audience)  # type: ignore[attr-defined]
    capabilities = _port(hub, CapabilityQueries)
    with hub.uows.snapshot() as snapshot:
        assert capabilities.get(snapshot, "ecg_rhythm").status == "unavailable"
