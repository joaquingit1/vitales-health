"""Pure measurement invariants: state machine, attempt effects, capability overlay (E05 3; CONTRACTS 4, 21)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.measurement.domain.errors import DomainRefusal
from crew_health.measurement.domain.model import (
    ACTIONS,
    MANUAL_METHODS,
    ORIGIN_MODES,
    REQUEST_STATES,
    TERMINAL_STATES,
    Attempt,
    CapabilityGap,
    KitCheck,
    MeasurementRequest,
    Requester,
    Viewer,
    allowed_actions,
    append_attempt,
    attempt_effect,
    capability_status,
    decide,
    gap_reason_for,
    may_accept,
    operator_relation_valid,
)

pytestmark = pytest.mark.trace("T-E05-01", "T-E05-03", "T-E05-04", "R07", "R08", "V05", "V11", "D08", "D11")

AT = datetime(2026, 11, 13, 9, 31, 10, tzinfo=UTC)
CAN = KitCheck(True, ("camera_station",), (), (), (), "kit can", "v1", "lunar_surface")
CANNOT = KitCheck(False, (), ("ecg_single_lead",), (), (), "kit cannot", "v1", "lunar_surface")
SUBJECT = Viewer("mara", False)
CREWMATE = Viewer("kai", False)
OFFICER = Viewer("cmo", True)
STRANGER = Viewer(None, False)


def request(
    state: str = "proposed", *, operator: str | None = "mara", requirement: str = "self"
) -> MeasurementRequest:
    return MeasurementRequest(
        request_id="mrq_1",
        revision=1,
        episode_id="epi_1",
        capability_code="pulse",
        reason="r",
        decision_affected="d",
        kit_check=CAN if state != "unavailable" else CANNOT,
        requester=Requester("person", "mara", None),
        patient_id="mara",
        operator_id=operator,
        consent_ref=None,
        state=state,  # type: ignore[arg-type]
        attempts=(),
        result_observation_ids=(),
        body_region="heart",
        created_at=AT,
        origin_mode="LIVE",
        mission_id="mis_1",
        release_id="rel_1",
        operator_requirement=requirement,  # type: ignore[arg-type]
        methods=("camera_estimate",),
        generation=1 if state in ("in_progress", "completed", "failed", "cancelled") else 0,
        unavailable_cause="no_capability" if state == "unavailable" else None,
    )


def attempt(sequence: int, outcome: str, disposition: str, observations: tuple[str, ...] = ()) -> Attempt:
    return Attempt(
        f"att_{sequence}",
        sequence,
        "adp_camera",
        1,
        AT,
        AT,
        outcome,  # type: ignore[arg-type]
        None if outcome == "success" else "too much movement",
        observations,
        (),
        disposition,  # type: ignore[arg-type]
        (),
        "fp",
    )


def test_enums_are_whole() -> None:
    assert ORIGIN_MODES == ("LIVE", "REPLAY", "SIM")
    assert len(REQUEST_STATES) == 9
    assert set(ACTIONS) == {"submit", "accept", "decline", "start", "cancel"}


def test_kit_refusal_is_always_unavailable() -> None:
    with pytest.raises(DomainRefusal) as caught:
        replace(request("unavailable"), state="proposed")
    assert caught.value.kind == "INTERNAL_ERROR"


def test_completed_requires_an_accepted_result() -> None:
    with pytest.raises(DomainRefusal):
        request("completed")


def test_failed_attempt_keeps_its_reason_and_never_references_records() -> None:
    with pytest.raises(DomainRefusal):
        Attempt("att_1", 1, "adp", 1, AT, AT, "failed", None, (), (), "not_usable", (), "fp")
    with pytest.raises(DomainRefusal):
        attempt(1, "failed", "not_usable", ("obs_1",))


def test_kit_check_shapes() -> None:
    with pytest.raises(DomainRefusal):
        KitCheck(True, (), (), (), (), "t", "v", "e")
    with pytest.raises(DomainRefusal):
        KitCheck(False, ("x",), (), (), (), "t", "v", "e")
    with pytest.raises(DomainRefusal):
        KitCheck(True, ("x",), (), (), (), "", "v", "e")


def test_gap_needs_consequence_and_plan_for_steps() -> None:
    with pytest.raises(DomainRefusal):
        CapabilityGap("gap_1", "epi", "ecg", "no_capability", "", (), None, "open", None, (), None, "rel", AT)
    with pytest.raises(DomainRefusal):
        CapabilityGap(
            "gap_1", "epi", "ecg", "no_capability", "c", (), None, "open", None, ("s",), None, "rel", AT
        )


def any_state(state: str) -> MeasurementRequest:
    if state != "completed":
        return request(state)
    done = attempt(1, "success", "usable", ("obs_1",))
    return replace(
        request("in_progress"), state="completed", attempts=(done,), result_observation_ids=("obs_1",)
    )


@given(
    st.sampled_from(REQUEST_STATES),
    st.sampled_from(ACTIONS),
    st.sampled_from([SUBJECT, CREWMATE, OFFICER, STRANGER]),
)
def test_decide_matches_allowed_actions(state: str, action: str, viewer: Viewer) -> None:
    r = any_state(state)
    allowed = allowed_actions(r, viewer)
    try:
        target = decide(r, action, viewer)  # type: ignore[arg-type]
    except DomainRefusal as error:
        assert action not in allowed
        assert error.kind in ("INVALID_TRANSITION", "ACTION_NOT_ALLOWED")
        if state in TERMINAL_STATES:
            assert error.kind == "INVALID_TRANSITION"
        return
    assert action in allowed
    assert target != state
    assert target not in ("completed", "failed")


def test_officer_status_is_not_a_request_grant() -> None:
    r = request("awaiting_acceptance", operator=None, requirement="crewmate")
    assert allowed_actions(r, OFFICER) == ("accept",)
    assert "decline" not in allowed_actions(r, CREWMATE)
    assert not may_accept(r, SUBJECT)
    officer_only = request("awaiting_acceptance", operator=None, requirement="crew_medical_officer")
    assert not may_accept(officer_only, CREWMATE)
    assert may_accept(officer_only, OFFICER)


@given(st.text(min_size=1, max_size=4), st.text(min_size=1, max_size=4))
def test_operator_relation(subject: str, operator: str) -> None:
    assert operator_relation_valid("self", subject, operator) == (subject == operator)
    for requirement in ("crewmate", "crew_medical_officer"):
        assert operator_relation_valid(requirement, subject, operator) == (subject != operator)


@given(
    st.sampled_from(REQUEST_STATES),
    st.sampled_from(("success", "failed", "rejected")),
    st.integers(min_value=0, max_value=4),
    st.integers(min_value=1, max_value=6),
    st.integers(min_value=0, max_value=6),
    st.integers(min_value=0, max_value=3),
)
def test_attempt_effect_never_fabricates(
    state: str, outcome: str, values: int, cap: int, prior: int, late: int
) -> None:
    base = any_state(state)
    terminal_run = base.state in TERMINAL_STATES and base.generation >= 1
    kept = tuple(attempt(i + 1, "failed", "not_usable") for i in range(prior))
    held = tuple(attempt(prior + i + 1, "failed", "quarantined") for i in range(late if terminal_run else 0))
    r = replace(base, attempts=base.attempts + kept + held)
    try:
        effect = attempt_effect(r, outcome=outcome, value_count=values, max_attempts=cap, max_late=3)  # type: ignore[arg-type]
    except DomainRefusal as error:
        assert error.kind == "INVALID_TRANSITION"
        if terminal_run:
            assert late >= 3
        else:
            assert r.state != "in_progress" or prior >= cap
        return
    if terminal_run:
        assert (effect.disposition, effect.state) == ("quarantined", r.state)
        return
    assert r.state == "in_progress"
    if effect.disposition == "usable":
        assert outcome == "success"
        assert values > 0
        assert effect.state == "completed"
    else:
        assert effect.disposition == "not_usable"
        assert effect.state == ("failed" if prior + 1 >= cap else "in_progress")


def test_retry_appends_and_keeps_failed_history() -> None:
    r = request("in_progress")
    failed = attempt(1, "failed", "not_usable")
    first = append_attempt(
        r, failed, attempt_effect(r, outcome="failed", value_count=0, max_attempts=4, max_late=4)
    )
    assert first.state == "in_progress" and first.revision == 2
    ok = attempt(2, "success", "usable", ("obs_1",))
    second = append_attempt(
        first, ok, attempt_effect(first, outcome="success", value_count=1, max_attempts=4, max_late=4)
    )
    assert second.state == "completed"
    assert second.attempts[0] == failed
    assert second.result_observation_ids == ("obs_1",)


def test_attempts_exhausted_fail_the_request() -> None:
    r = request("in_progress")
    effect = attempt_effect(r, outcome="rejected", value_count=1, max_attempts=1, max_late=1)
    assert (effect.disposition, effect.state) == ("not_usable", "failed")


@given(
    st.booleans(),
    st.lists(st.sampled_from(sorted(MANUAL_METHODS | {"camera_estimate", "device"})), min_size=1, max_size=3),
    st.one_of(st.none(), st.frozensets(st.sampled_from(["camera_estimate", "device"]))),
)
def test_capability_status_never_promotes_or_guesses(
    kit: bool, methods: list[str], ready: frozenset[str] | None
) -> None:
    status, reason = capability_status(kit_available=kit, methods=tuple(methods), ready_methods=ready)
    if not kit:
        assert (status, reason) == ("unavailable", "not_in_kit")
    elif any(m in MANUAL_METHODS for m in methods):
        assert status == "available"
    elif ready is None or not set(methods) & ready:
        assert (status, reason) == ("unavailable", "adapter_down")


def test_gap_reasons() -> None:
    assert gap_reason_for("not_in_kit") == "no_capability"
    assert gap_reason_for("adapter_down") == "device_absent"
    assert gap_reason_for("operator_absent") == "operator_absent"
    assert gap_reason_for("context_inapplicable") == "context_inapplicable"
