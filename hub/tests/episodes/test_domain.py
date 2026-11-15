from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.episodes.domain.derivation import derive
from crew_health.episodes.domain.onset import Onset
from crew_health.episodes.domain.resolution import check_resolution
from crew_health.episodes.domain.types import (
    Episode,
    EpisodeState,
    EvidenceObservation,
    LinkFact,
    ResolutionCommand,
    ResolutionFacts,
    ResolutionReason,
    TaskFact,
)
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor

NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)
EP = Episode(
    "epi_one",
    "person_a",
    "mission_one",
    "SIM",
    "token",
    Onset(None, "unknown"),
    EpisodeState.OPEN,
    1,
    NOW,
    Actor("person", "person_a"),
    "v_one",
    ("red_one",),
    None,
    "rel_one",
)
FACTS = ResolutionFacts(False, False, False, False, False, (), None, None, None, None, None)
CMD = ResolutionCommand(ResolutionReason.SYMPTOMS_REPORTED_RESOLVED, None, (), None, "person_a", "patient")


@pytest.mark.trace("T-E15-043", "R18")
@given(
    st.lists(st.sampled_from(["review", "custom", "review_item"]), max_size=20),
    st.lists(st.sampled_from(["proposed", "accepted", "in_progress", "completed", "cancelled"]), max_size=20),
)
def test_derivation_precedence(tasks: list[str], states: list[str]) -> None:
    result = derive(
        [TaskFact(k, "open") for k in tasks], [LinkFact("measurement_request", s) for s in states]
    )
    expected = (
        EpisodeState.AWAITING_REVIEW
        if "review" in tasks
        else EpisodeState.AWAITING_INFORMATION
        if any(s in ("proposed", "accepted", "in_progress") for s in states)
        else EpisodeState.FOLLOWUP
        if tasks
        else EpisodeState.OPEN
    )
    assert result == expected


@pytest.mark.trace("T-E15-005", "R28")
@pytest.mark.parametrize(
    "authority,actor,allowed",
    [
        ("patient", "person_a", True),
        ("crew_medical_officer", "officer_a", False),
        ("ground", "ground_one", False),
        ("assistant", "assistant_one", False),
    ],
)
def test_symptom_resolution_only_patient(authority: str, actor: str, allowed: bool) -> None:
    cmd = replace(CMD, authority=authority, actor_id=actor)
    if allowed:
        check_resolution(EP, cmd, FACTS)
    else:
        with pytest.raises(ProblemError):
            check_resolution(EP, cmd, FACTS)


@pytest.mark.trace("T-E15-008", "T-E15-007")
@pytest.mark.parametrize(
    "facts",
    [
        replace(FACTS, blocks_resolution=True),
        replace(FACTS, open_tasks=True),
        replace(FACTS, open_plan_runs=True),
        replace(FACTS, pending_requests=True),
    ],
)
def test_resolution_common_guards(facts: ResolutionFacts) -> None:
    with pytest.raises(ProblemError):
        check_resolution(EP, CMD, facts)


@pytest.mark.trace("T-E15-006", "V02")
def test_anomaly_needs_real_linked_evidence_and_no_symptom() -> None:
    obs = EvidenceObservation("obs_one", "person_a", "epi_one", "accepted", NOW + timedelta(seconds=1))
    cmd = replace(
        CMD,
        reason=ResolutionReason.MEASUREMENT_ANOMALY_NOT_REPRODUCED,
        rationale="token",
        observation_ids=("obs_one",),
    )
    check_resolution(EP, cmd, replace(FACTS, observations=(obs,)))
    for facts in (
        FACTS,
        replace(FACTS, present_answer=True, observations=(obs,)),
        replace(FACTS, observations=(replace(obs, patient_id="person_b"),)),
        replace(FACTS, observations=(replace(obs, observed_at=NOW),)),
    ):
        with pytest.raises(ProblemError):
            check_resolution(EP, cmd, facts)


@pytest.mark.trace("T-E15-013", "T-E15-005")
def test_clinician_requires_own_onboard_review_after_correction() -> None:
    cmd = replace(
        CMD,
        reason=ResolutionReason.CLINICIAN_ASSESSED,
        authority="crew_medical_officer",
        actor_id="officer_a",
        rationale="token",
        review_id="rev_one",
    )
    facts = replace(
        FACTS, review_id="rev_one", review_by="officer_a", review_role="crew_medical_officer", review_at=NOW
    )
    check_resolution(EP, cmd, facts)
    for f in (
        replace(facts, review_role="ground"),
        replace(facts, review_by="officer_b"),
        replace(facts, latest_correction_at=NOW + timedelta(seconds=1)),
    ):
        with pytest.raises(ProblemError):
            check_resolution(EP, cmd, f)
    with pytest.raises(ProblemError):
        check_resolution(EP, replace(cmd, actor_id="person_a"), facts)


@pytest.mark.trace("T-E15-044")
def test_onset_pair_and_future() -> None:
    with pytest.raises(ProblemError):
        Onset(NOW, "unknown")
    with pytest.raises(ProblemError):
        Onset(None, "exact")
    with pytest.raises(ProblemError):
        Onset(NOW + timedelta(seconds=1), "exact").validate_at(NOW)


@pytest.mark.trace("T-E15-019", "T-E15-042")
def test_due_time_modes_and_ucum() -> None:
    from decimal import Decimal

    from crew_health.episodes.domain.due_policy import DuePolicy, due_time, duration_seconds

    assert duration_seconds(Decimal(1), "h") == 3600
    assert duration_seconds(Decimal(1), "min") == 60
    assert duration_seconds(Decimal(1), "d") == 86400
    assert due_time(DuePolicy("p", "none"), NOW) == (None, "none")
    assert due_time(DuePolicy("p", "after"), NOW) == (None, "threshold_pending")
    assert due_time(DuePolicy("p", "after", Decimal(60)), NOW) == (NOW + timedelta(minutes=1), "set")
    assert due_time(DuePolicy("p", "end_of_mission_day"), NOW, day_end=NOW + timedelta(hours=1))[
        0
    ] == NOW + timedelta(hours=1)
    assert due_time(
        DuePolicy("p", "chosen_by_person", minimum_seconds=Decimal(1), maximum_seconds=Decimal(60)), NOW
    ) == (None, "none")
    for invalid in (Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ProblemError):
            duration_seconds(invalid, "s")
    with pytest.raises(ProblemError):
        duration_seconds(Decimal(1), "mg")
    with pytest.raises(ProblemError):
        due_time(DuePolicy("p", "end_of_mission_day"), NOW)
    with pytest.raises(ProblemError):
        due_time(DuePolicy("p", "none"), NOW, chosen=NOW)
    with pytest.raises(ProblemError):
        due_time(
            DuePolicy("p", "chosen_by_person", minimum_seconds=Decimal(1), maximum_seconds=Decimal(60)),
            NOW,
            chosen=NOW + timedelta(seconds=61),
        )


@pytest.mark.trace("T-E15-001", "T-E15-009")
@given(
    st.lists(
        st.sampled_from(["draft", "open", "awaiting_information", "awaiting_review", "followup", "resolved"]),
        min_size=1,
        max_size=20,
    )
)
def test_no_nonresolve_transition_resolves(states: list[str]) -> None:
    from crew_health.episodes.domain.state_machine import next_state

    for state in states:
        ep = replace(EP, state=EpisodeState(state))
        if ep.state != EpisodeState.RESOLVED:
            assert next_state(ep, [], [], answered=frozenset({"red_one"})) != EpisodeState.RESOLVED
        else:
            assert (
                next_state(ep, [], [], answered=frozenset({"red_one"}), reopen=True) != EpisodeState.RESOLVED
            )


@pytest.mark.trace("T-E15-006", "R28")
def test_resolution_accepts_the_real_records_quality_vocabulary() -> None:
    observation = EvidenceObservation(
        "obs_one", "person_a", "epi_one", "accepted", NOW + timedelta(seconds=1)
    )
    cmd = replace(
        CMD,
        reason=ResolutionReason.MEASUREMENT_ANOMALY_NOT_REPRODUCED,
        rationale="token",
        observation_ids=("obs_one",),
    )
    check_resolution(EP, cmd, replace(FACTS, observations=(observation,)))
