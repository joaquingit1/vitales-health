from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.episodes.application.codec import array, decode, integer, obj, text
from crew_health.episodes.application.dto import AnswerFact, LinkedFact, NewEpisode, PersonRef, Viewer
from crew_health.episodes.application.queries import (
    EpisodeReaderFacade,
    KernelFacade,
    RefCheckFacade,
    ToolFactsFacade,
)
from crew_health.episodes.application.rules import parse_rules
from crew_health.episodes.domain.due_policy import DuePolicy, due_time, duration_seconds
from crew_health.episodes.domain.resolution import check_resolution
from crew_health.episodes.domain.state_machine import next_state
from crew_health.episodes.domain.types import EpisodeState, ResolutionReason
from crew_health.kernel.errors import ProblemError

from .builders import GROUND, NOW, OFFICER, OTHER, PATIENT, ROOT, Station, station
from .test_domain import CMD, EP, FACTS


@pytest.fixture
def s(tmp_path: Path) -> Station:
    return station(tmp_path)


@pytest.mark.trace("T-E15-042")
@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "unknown_kind",
        "pending",
        "provenance",
        "assignment",
        "keys",
        "placeholder",
        "reviewed",
        "negative_late",
        "reverse_bounds",
    ],
)
def test_rules_refuse_invalid_flight_tables(mutation: str) -> None:
    data = json.loads((ROOT / "config/episode-rules.json").read_bytes())
    if mutation == "duplicate":
        data["due_policies"].append(data["due_policies"][0])
    elif mutation == "unknown_kind":
        data["due_policies"][0]["kind"] = "other"
    elif mutation == "pending":
        data["due_policies"][0]["status"] = "set"
    elif mutation == "provenance":
        data["due_policies"][0]["basis"] = ""
    elif mutation == "assignment":
        data["task_policies"]["review"] = "missing"
    elif mutation == "keys":
        del data["task_templates"]["review_assessment"]
    elif mutation == "placeholder":
        data["task_templates"]["review_assessment"] = "{patient}"
    elif mutation == "reviewed":
        data["review_state"] = "reviewed"
    elif mutation == "negative_late":
        data["late_entry_after"]["value"] = -1
    else:
        data["due_policies"][2]["min"]["value"] = 999999
    with pytest.raises(ProblemError):
        parse_rules(json.dumps(data).encode())


@pytest.mark.trace("T-E15-035", "T-E15-045", "T-E15-046", "V26")
def test_canonical_consumers_and_reference_only_tool_context(s: Station) -> None:
    ep = s.create()
    answer = s.answer(ep)
    kernel = KernelFacade(s.service, s.queries)
    tools = ToolFactsFacade(s.queries)
    with s.db.begin(actor=PATIENT.actor, correlation_id="corr_seam") as uow:
        initial_fact = kernel.facts(uow, ep)
        assert initial_fact is not None and initial_fact.patient_id == "person_a"
        assert kernel.facts(uow, "unknown") is None
        kernel.record_route(uow, ep, "module_one", answer, NOW)
        fact = s.queries.get(uow, ep)
        assert fact is not None and fact.route_module_id == "module_one"
        assert s.queries.visible_to(uow, ep, PATIENT.actor)
        assert not s.queries.visible_to(uow, ep, OTHER.actor)
        assert s.queries.episode_of(uow, "red_flag", ep) == ep
        assert s.queries.episode_of(uow, "unknown", "missing") is None
        assert tools.subject_of(uow, ep) == "person_a"
        result = tools.facts(uow, ep, frozenset({"concern", "answers", "journal", "private_report"}))
        assert result.unavailable == frozenset({"journal", "private_report"})
        assert set(result.sections) == {"concern", "answers"}
        with pytest.raises(ProblemError):
            kernel.record_route(uow, ep, "module_one", "unlinked", NOW)
    reader = EpisodeReaderFacade(s.queries)
    refs = RefCheckFacade(s.queries)
    assert reader.subject_of(ep) == "person_a"
    assert reader.subject_of("unknown") is None
    visible = reader.visible(PATIENT, ep)
    assert visible is not None and visible.episode_id == ep
    assert reader.visible(OTHER, ep) is None
    assert reader.visible(PATIENT, "unknown") is None
    assert refs.visible(ep, PATIENT.actor)
    assert not refs.visible(ep, OTHER.actor)
    with s.db.begin(actor=OTHER.actor, correlation_id="corr_seam") as uow:
        with pytest.raises(ProblemError):
            tools.facts(uow, ep, frozenset({"concern"}))
        with pytest.raises(ProblemError):
            kernel.record_route(uow, ep, "module_one", answer, NOW)


@pytest.mark.trace("T-E15-014", "T-E15-047", "T-E15-033")
def test_keyset_pages_scope_and_no_writes_on_get(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    other = s.create()
    s.answer(other)
    with s.db.snapshot() as view:
        before = view.connection.rows("SELECT count(*) FROM episodes_change", (), limit=1)
        first = s.queries.list_episodes(view, OFFICER, None, None, 1)
        assert obj(first["page"])["has_more"] is True
        cursor = str(obj(first["page"])["next_cursor"])
        second = s.queries.list_episodes(view, OFFICER, None, cursor, 1)
        assert obj(second["page"])["has_more"] is False
        page = s.queries.timeline(view, PATIENT, ep, None, 1)
        assert page["next_cursor"] is not None
        following = s.queries.timeline(view, PATIENT, ep, str(page["next_cursor"]), 200)
        assert following["items"]
        for invalid_cursor, limit in (("bad", 1), (None, 0), (None, 201)):
            with pytest.raises(ProblemError):
                s.queries.timeline(view, PATIENT, ep, invalid_cursor, limit)
        for states, limit in ((("unknown",), 1), ((), 0), ((), 201)):
            with pytest.raises(ProblemError):
                s.queries.list_episodes(view, PATIENT, None, None, limit, states)
        assert s.queries.list_episodes(view, PATIENT, None, None, 200, ("open",))["items"]
        with pytest.raises(ProblemError):
            s.queries.list_episodes(view, GROUND, None, None, 20)
        assert view.connection.rows("SELECT count(*) FROM episodes_change", (), limit=1) == before


@pytest.mark.trace("T-E15-013", "T-E15-048", "T-E15-044")
def test_action_authority_validation_and_missing_dependencies(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "invalid"})
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "add_followup", "action_text": "token", "owner_person_id": "person_b"})
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "add_followup", "action_text": " "})
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "correct_onset", "onset": {"at": None, "precision": "unknown"}}, session=OFFICER)
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "reopen", "reason": "symptoms_recurred"})
    s.act(ep, {"action": "add_followup", "action_text": "token"})
    with s.db.snapshot() as view:
        task = s.queries.tasks(view, ep)[0]
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "complete_followup", "task_id": task["task_id"], "task_revision": 2})
    with pytest.raises(ProblemError):
        s.act(
            ep,
            {
                "action": "complete_followup",
                "task_id": task["task_id"],
                "task_revision": 1,
                "evidence_ref": "unknown",
            },
        )
    s.act(
        ep,
        {
            "action": "reassign_followup",
            "task_id": task["task_id"],
            "task_revision": 1,
            "owner_person_id": "officer_a",
        },
    )
    s.act(ep, {"action": "complete_followup", "task_id": task["task_id"], "task_revision": 2})
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "complete_followup", "task_id": task["task_id"], "task_revision": 3})
    s.service.providers.urgency = None
    with pytest.raises(ProblemError) as err:
        s.act(ep, {"action": "resolve", "reason": "symptoms_reported_resolved"})
    assert err.value.retryable


@pytest.mark.trace("T-E15-020", "T-E15-012", "T-E15-033")
def test_officer_patient_cannot_review_or_acknowledge_and_body_scope(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    self_officer = replace(PATIENT, roles=OFFICER.roles)
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "acknowledge"}, session=self_officer)
    body = {
        "decisions": [{"item": "t", "decision": "agree", "rationale": "t"}],
        "unresolved": [],
        "assessment_id": "asm_other",
    }
    with s.db.begin(actor=OFFICER.actor, correlation_id="corr_review") as uow:
        with pytest.raises(ProblemError):
            s.service.review(
                uow, OFFICER, ep, body, 2, s.identity("cmd_missing", body, ep, "recordReview", OFFICER)
            )
    s.service.providers.roster = None
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "request_review"})


@pytest.mark.trace("T-E15-019", "T-E15-005", "T-E15-001")
def test_remaining_pure_negative_constraints() -> None:
    with pytest.raises(ProblemError):
        duration_seconds(Decimal(999999999999), "d")
    with pytest.raises(ProblemError):
        due_time(DuePolicy("p", "after", Decimal(60)), NOW, chosen=NOW)
    with pytest.raises(ProblemError):
        due_time(DuePolicy("p", "unknown"), NOW)
    assert (
        due_time(
            DuePolicy("p", "chosen_by_person", minimum_seconds=Decimal(1), maximum_seconds=Decimal(60)),
            NOW,
            chosen=NOW + timedelta(seconds=1),
        )[1]
        == "set"
    )
    with pytest.raises(ProblemError):
        check_resolution(replace(EP, state=EpisodeState.DRAFT), CMD, FACTS)
    with pytest.raises(ProblemError):
        check_resolution(EP, replace(CMD, observation_ids=("obs_one",)), FACTS)
    with pytest.raises(ProblemError):
        check_resolution(EP, replace(CMD, reason=ResolutionReason.CLINICIAN_ASSESSED, rationale=" "), FACTS)
    with pytest.raises(ProblemError):
        check_resolution(EP, replace(CMD, reason=ResolutionReason.CLINICIAN_ASSESSED, rationale="t"), FACTS)
    with pytest.raises(ProblemError):
        check_resolution(
            EP,
            replace(
                CMD,
                reason=ResolutionReason.MEASUREMENT_ANOMALY_NOT_REPRODUCED,
                rationale="t",
                observation_ids=(),
            ),
            FACTS,
        )
    assert (
        next_state(replace(EP, state=EpisodeState.RESOLVED), [], [], answered=frozenset())
        == EpisodeState.RESOLVED
    )
    for fun, value in ((text, None), (integer, "1"), (obj, []), (array, {}), (decode, "bad")):
        with pytest.raises(ProblemError):
            fun(value)


@pytest.mark.trace("T-E15-020", "T-E15-033", "T-E15-034", "T-E15-035")
def test_review_retry_clinician_resolution_and_pure_viewer_identity(s: Station) -> None:
    from .test_workers import Assessments

    s.service.providers.assessments = Assessments()
    s.service.providers.assessment_commands = Assessments()
    ep = s.create()
    s.answer(ep)
    body = {
        "decisions": [{"item": "token", "decision": "agree", "rationale": "token"}],
        "unresolved": [],
        "assessment_id": "asm_one",
    }
    identity = s.identity("cmd_review_repeat", body, ep, "recordReview", OFFICER)
    with s.db.begin(actor=OFFICER.actor, correlation_id="corr_review") as uow:
        first = s.service.review(uow, OFFICER, ep, body, 2, identity)
        second = s.service.review(uow, OFFICER, ep, body, 2, identity)
        assert first == second
    with s.db.begin(actor=PATIENT.actor, correlation_id="corr_view") as uow:
        assert (
            KernelFacade(s.service, s.queries).view(
                uow, Viewer("crew", "person_a", frozenset({"crew"}), PATIENT.actor), ep
            )["patient_id"]
            == "person_a"
        )
        with pytest.raises(ProblemError):
            KernelFacade(s.service, s.queries).view(uow, Viewer("crew", "person_a", frozenset({"crew"})), ep)
    s.act(
        ep,
        {
            "action": "resolve",
            "reason": "clinician_assessed",
            "rationale": "token",
            "review_id": first["review_id"],
        },
        session=OFFICER,
    )
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "add_followup", "action_text": "token"})
    s.act(ep, {"action": "reopen", "reason": "symptoms_recurred"})
    with s.db.snapshot() as view:
        assert s.service.repo.require(view, ep).state.value == "open"


@pytest.mark.trace("T-E15-013", "T-E15-025", "T-E15-012")
def test_invalid_commands_are_atomic_and_unauthorized_identities_fail(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    with s.db.begin(actor=PATIENT.actor, correlation_id="corr_invalid") as uow:
        aggregate = s.service.repo.require(uow, ep)
        with pytest.raises(ProblemError):
            s.service.version(aggregate, None)
        with pytest.raises(ProblemError):
            s.service.human(GROUND, aggregate)
        with pytest.raises(ProblemError):
            s.service.human(OTHER, aggregate)
        with pytest.raises(ProblemError):
            s.service.replay(uow, OTHER, aggregate, s.identity("cmd_wrong", {}, ep))
        with pytest.raises(ProblemError):
            s.service.act(uow, OFFICER, ep, {"action": "acknowledge"}, 2, s.identity("cmd_actor", {}, ep))
        with pytest.raises(ProblemError):
            s.service.review(uow, OFFICER, ep, {}, 2, s.identity("cmd_actor", {}, ep))
        with pytest.raises(ProblemError):
            s.service.record(uow, "invalid", "ref_one", aggregate, {})
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "resolve", "reason": "bad"})
    with pytest.raises(ProblemError):
        s.act(
            ep,
            {
                "action": "resolve",
                "reason": "measurement_anomaly_not_reproduced",
                "rationale": "t",
                "observation_ids": ["obs_one"],
            },
        )


@pytest.mark.trace("T-E15-042", "T-E15-023", "T-E15-032")
def test_creation_refuses_wrong_namespace_author_and_invalid_content(s: Station) -> None:
    from crew_health.episodes.domain.onset import Onset

    draft = NewEpisode(
        patient_id="person_a",
        mission_id="mission_one",
        origin_mode="SIM",
        concern_text="t",
        onset=Onset(None, "unknown"),
        created_by=PersonRef("person_a", "A"),
        questionnaire_version="0" * 64,
        red_flag_ids=("red_one",),
        config_release_id="release_1",
        received_at=NOW,
    )
    invalid = (
        replace(draft, created_by=PersonRef("person_b", "B")),
        replace(draft, origin_mode="LIVE"),
        replace(draft, concern_text=" "),
        replace(draft, red_flag_ids=("red_one", "red_one")),
    )
    for item in invalid:
        with (
            pytest.raises(ProblemError),
            s.db.begin(actor=PATIENT.actor, correlation_id="corr_create") as uow,
        ):
            s.service.create(uow, item)
    s.service.providers.origin = None
    with pytest.raises(ProblemError), s.db.begin(actor=PATIENT.actor, correlation_id="corr_create") as uow:
        s.service.create(uow, draft)


@pytest.mark.trace("T-E15-042")
def test_immutable_rules_pins_and_cache_bounds(s: Station) -> None:
    raw = (ROOT / "config/episode-rules.json").read_bytes()
    with pytest.raises(ProblemError):
        parse_rules(b" " * 1048577)
    invalid = json.loads(raw)
    invalid["due_policies"][0]["duration"]["value"] = True
    with pytest.raises(ProblemError):
        parse_rules(json.dumps(invalid).encode())
    invalid = json.loads(raw)
    invalid["task_templates"]["review_requested"] = ""
    with pytest.raises(ProblemError):
        parse_rules(json.dumps(invalid).encode())
    s.service.rules.activate("release_two", raw)
    with pytest.raises(ProblemError):
        s.service.rules.activate("release_three", raw)
    changed = json.loads(raw)
    changed["notice"] = "changed"
    with pytest.raises(ProblemError):
        s.service.rules.activate("release_1", json.dumps(changed).encode())
    with pytest.raises(ProblemError):
        s.service.rules.rules("missing")


@pytest.mark.trace("T-E15-021", "T-E15-032")
def test_event_replays_unknown_refs_and_same_record_cross_episode_are_safe(s: Station) -> None:
    ep = s.create()
    s.answer(ep)
    ep2 = s.create()
    s.answer(ep2)
    with s.db.begin(actor=PATIENT.actor, correlation_id="corr_events") as uow:
        s.handlers.answer(uow, AnswerFact("missing", "ans_missing", "red_one", "SIM", NOW))
        s.handlers.answer(uow, AnswerFact(ep, "ans_wrong", "red_one", "LIVE", NOW))
        event = AnswerFact(ep, "ans_duplicate", "red_one", "SIM", NOW)
        s.handlers.answer(uow, event)
        s.handlers.answer(uow, event)
        s.handlers.linked(
            uow, LinkedFact("missing_event", "missing", "observation", "obs_missing", "ok", "SIM", NOW)
        )
        with pytest.raises(ProblemError):
            s.handlers.linked(uow, LinkedFact("invalid_event", ep, "invalid", "invalid", "ok", "SIM", NOW))
        s.handlers.linked(uow, LinkedFact("one_link", ep, "observation", "obs_one", "ok", "SIM", NOW))
        assert s.queries.episode_of(uow, "observation", "obs_one") == ep
    with pytest.raises(ProblemError), s.db.begin(actor=PATIENT.actor, correlation_id="corr_conflict") as uow:
        s.handlers.linked(uow, LinkedFact("second_link", ep2, "observation", "obs_one", "ok", "SIM", NOW))


@pytest.mark.trace("T-E15-042", "R43")
def test_owned_rules_schema_and_semantic_loader_reject_unknown_fields() -> None:
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (ROOT / "hub/src/crew_health/episodes/api/schemas/config-episode-rules.schema.json").read_bytes()
    )
    data = json.loads((ROOT / "config/episode-rules.json").read_bytes())
    Draft202012Validator(schema).validate(data)
    data["new_safety_switch"] = False
    assert list(Draft202012Validator(schema).iter_errors(data))
    with pytest.raises(ProblemError):
        parse_rules(json.dumps(data).encode())
