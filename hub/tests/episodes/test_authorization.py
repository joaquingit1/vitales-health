from pathlib import Path

import pytest

from .builders import station


@pytest.mark.trace("V12", "V25", "D09")
def test_officer_role_never_substitutes_for_object_grant(tmp_path: Path) -> None:
    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.facts.shared = False
    response = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "officer"})
    assert response.status_code == 404
    assert "private-concern-canary" not in response.text


@pytest.mark.trace("V12", "V25", "D09")
def test_ground_consent_alone_never_reads_live_chart(tmp_path: Path) -> None:
    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.facts.consent = True
    response = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "ground"})
    assert response.status_code == 403
    assert "private-concern-canary" not in response.text


@pytest.mark.trace("R10", "D09", "V25")
def test_role_without_current_clinical_authority_cannot_acknowledge(tmp_path: Path) -> None:
    from crew_health.kernel.errors import ProblemError

    from .builders import OFFICER

    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.facts.authority_kind = "none"
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "acknowledge"}, session=OFFICER)


@pytest.mark.trace("R18", "D09", "V25")
def test_acting_officer_can_review_but_cannot_resolve_as_clinician(tmp_path: Path) -> None:
    from dataclasses import replace

    from .builders import OFFICER, PATIENT

    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.facts.authority_kind = "acting_medical_officer"
    acting = replace(OFFICER, roles=PATIENT.roles)
    with s.db.begin(actor=acting.actor, correlation_id="corr_acting") as uow:
        result = s.service.review(
            uow, acting, ep, {}, 2, s.identity("cmd_acting", {}, ep, "recordReview", acting)
        )
    assert result["authority"] == "acting_medical_officer"
    assert result["role_assignment_id"] == "rol_authority"
    from crew_health.kernel.errors import ProblemError

    with pytest.raises(ProblemError):
        s.act(
            ep,
            {
                "action": "resolve",
                "reason": "clinician_assessed",
                "review_id": result["review_id"],
                "rationale": "explicit",
            },
            session=acting,
        )


@pytest.mark.trace("V12", "V25", "D09")
def test_ground_reads_only_immutable_delivered_selected_fields(tmp_path: Path) -> None:
    from datetime import timedelta
    from hashlib import sha256

    from crew_health.episodes.application.ports import DeliveredEpisodeProjection
    from crew_health.kernel.commands import canonical_json
    from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork

    from .builders import NOW

    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    raw = canonical_json({"concern_text": "approved earlier concern", "timeline": [], "reviews": []})
    frozen = DeliveredEpisodeProjection(
        "hnd_approved",
        sha256(raw).hexdigest(),
        ep,
        "ground",
        frozenset({"concern_text", "timeline", "reviews"}),
        raw,
        NOW - timedelta(hours=2),
        NOW - timedelta(hours=1),
    )

    class Delivered:
        def read(
            self, view: ReadSnapshot | UnitOfWork, session: SessionContext, *, episode_id: str
        ) -> DeliveredEpisodeProjection | None:
            assert episode_id == ep and session.actor.id == "ground"
            return frozen

    s.service.providers.delivered = Delivered()
    s.service.providers.answers = None
    s.service.providers.urgency = None
    with s.db.snapshot() as view:
        before = view.connection.rows("SELECT count(*) FROM episodes_change", (), limit=1)
    response = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "ground"})
    assert response.status_code == 200, response.text
    assert response.json()["projection_kind"] == "delivered_episode"
    assert response.json()["fields"] == {
        "concern_text": "approved earlier concern",
        "timeline": [],
        "reviews": [],
    }
    assert "private-concern-canary" not in response.text
    assert "state" not in response.json()["fields"]
    for suffix, field in (("/timeline", "timeline"), ("/reviews", "reviews")):
        response = s.client.get("/v1/episodes/" + ep + suffix, headers={"Authorization": "ground"})
        assert response.status_code == 200, response.text
        assert response.json()["fields"] == {field: []}
    with s.db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM episodes_change", (), limit=1) == before
    s.service.providers.delivered = None
    assert s.client.get("/v1/episodes/" + ep, headers={"Authorization": "ground"}).status_code == 403


@pytest.mark.trace("V12", "V25", "D09")
def test_unshared_history_lists_and_retry_do_not_leak(tmp_path: Path) -> None:
    from .builders import OFFICER

    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.act(ep, {"action": "acknowledge"}, session=OFFICER, key="cmd_granted")
    s.facts.shared = False
    for suffix in ("", "/timeline", "/reviews"):
        response = s.client.get("/v1/episodes/" + ep + suffix, headers={"Authorization": "officer"})
        assert response.status_code == 404
        assert "private-concern-canary" not in response.text
    assert s.client.get("/v1/episodes", headers={"Authorization": "officer"}).json()["items"] == []
    response = s.client.post(
        "/v1/episodes/" + ep + "/actions",
        json={"action": "acknowledge"},
        headers={"Authorization": "officer", "Idempotency-Key": "cmd_granted", "If-Match": "1"},
    )
    assert response.status_code == 404
    assert response.json()["current_state"] is None


@pytest.mark.trace("V12", "V25", "D09")
def test_answer_field_grant_withholds_only_unshared_answer(tmp_path: Path) -> None:
    from crew_health.kernel.errors import ErrorCode, ProblemError
    from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork

    s = station(tmp_path)
    ep = s.create()
    answer = s.answer(ep)

    class Fields:
        def require_read(
            self,
            view: ReadSnapshot | UnitOfWork,
            session: SessionContext,
            *,
            object_ref: str,
            patient_id: str,
            fields: frozenset[str],
        ) -> None:
            if object_ref == answer and "value" in fields:
                raise ProblemError(ErrorCode.NOT_FOUND, "field not selected")

    s.service.providers.authorization = Fields()
    response = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "officer"})
    assert response.status_code == 200, response.text
    assert response.json()["answers"] == []
    assert response.json()["withheld_answer_count"] == 1
    assert (
        len(s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient"}).json()["answers"]) == 1
    )


@pytest.mark.trace("T-E15-035", "V25")
def test_record_authorization_structural_copy_has_exact_owner_signature() -> None:
    import inspect

    from crew_health.episodes.application.ports import RecordAuthorization as Consumer
    from crew_health.records.application.ports import RecordAuthorization as Owner

    assert inspect.signature(Consumer.require_read) == inspect.signature(Owner.require_read)


@pytest.mark.trace("R25", "T-E15-030")
def test_repeated_ack_history_never_breaks_legacy_read(tmp_path: Path) -> None:
    from dataclasses import replace

    from .builders import OFFICER

    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.service.limits = replace(s.service.limits, max_links_per_episode=2)
    for index in range(3):
        s.act(ep, {"action": "acknowledge"}, session=OFFICER, key="ack_" + str(index))
    response = s.client.get(
        "/v1/episodes/" + ep, headers={"Authorization": "patient", "Accept": "application/json"}
    )
    assert response.status_code == 200, response.text
    extended = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient"})
    assert extended.status_code == 200, extended.text
    cursor = extended.json()["history_cursors"]["ack"]
    assert cursor is not None
    first = extended.json()["acknowledgments"]
    page = s.client.get(
        "/v1/episodes/" + ep + "/history?kind=ack&limit=2&cursor=" + cursor,
        headers={"Authorization": "patient"},
    )
    assert page.status_code == 200, page.text
    assert len(first) == 2 and len(page.json()["items"]) == 1
    assert page.json()["next_cursor"] is None
    assert len({item["ack_id"] for item in first + page.json()["items"]}) == 3


@pytest.mark.trace("T-E15-011", "D40")
def test_ground_advice_does_not_derive_from_undelivered_answer_head(tmp_path: Path) -> None:
    from crew_health.episodes.application.dto import Answer

    from .builders import NOW

    s = station(tmp_path)
    ep = s.create()
    s.facts.rows.append(
        Answer("answer_delayed", "red_one", "0" * 64, "absent", "person_a", ep, None, NOW, "SIM")
    )
    s.facts.consent = True
    s.service.providers.answers = None
    response = s.client.post(
        "/v1/episodes/" + ep + "/reviews",
        headers={"Authorization": "ground", "Idempotency-Key": "ground_advice", "If-Match": "1"},
        json={
            "decisions": [{"item": "context", "decision": "not_assessed", "rationale": "No assessment"}],
            "unresolved": [],
        },
    )
    assert response.status_code == 201, response.text
    with s.db.snapshot() as view:
        assert s.service.repo.require(view, ep).state.value == "draft"


@pytest.mark.trace("V12", "V25", "D09")
@pytest.mark.parametrize("fault", ["episode", "recipient", "hash", "future", "fields", "missing_field"])
def test_delivered_snapshot_provenance_and_selected_fields_fail_closed(tmp_path: Path, fault: str) -> None:
    from dataclasses import replace
    from datetime import timedelta
    from hashlib import sha256

    from crew_health.episodes.application.ports import DeliveredEpisodeProjection
    from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork

    from .builders import NOW

    s = station(tmp_path)
    ep = s.create()
    raw = b'{"concern_text":"approved"}'
    frozen = DeliveredEpisodeProjection(
        "hnd_approved", sha256(raw).hexdigest(), ep, "ground", frozenset({"concern_text"}), raw, NOW, NOW
    )
    if fault == "episode":
        frozen = replace(frozen, episode_id="epi_other")
    elif fault == "recipient":
        frozen = replace(frozen, recipient_id="someone_else")
    elif fault == "hash":
        frozen = replace(frozen, content_sha256="0" * 64)
    elif fault == "future":
        frozen = replace(frozen, delivered_at=NOW + timedelta(seconds=1))
    elif fault == "fields":
        frozen = replace(frozen, selected_fields=frozenset({"answers"}))

    class Delivered:
        def read(
            self, view: ReadSnapshot | UnitOfWork, session: SessionContext, *, episode_id: str
        ) -> DeliveredEpisodeProjection | None:
            return frozen

    s.service.providers.delivered = Delivered()
    suffix = "/timeline" if fault == "missing_field" else ""
    response = s.client.get("/v1/episodes/" + ep + suffix, headers={"Authorization": "ground"})
    assert response.status_code == (403 if fault == "missing_field" else 500), response.text
    assert "approved" not in response.text
    assert response.json()["retryable"] == (fault != "missing_field")


@pytest.mark.trace("V12", "V25", "D09")
def test_missing_grant_and_authority_providers_fail_closed(tmp_path: Path) -> None:
    from crew_health.kernel.errors import ProblemError

    from .builders import OFFICER

    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.service.providers.authorization = None
    assert s.client.get("/v1/episodes/" + ep, headers={"Authorization": "officer"}).status_code == 404
    s.service.providers.authorization = s.facts
    s.service.providers.clinical_authority = None
    response = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "officer"})
    assert response.status_code == 200
    assert "record_review" not in response.json()["allowed_actions"]
    with pytest.raises(ProblemError):
        s.act(ep, {"action": "acknowledge"}, session=OFFICER)


@pytest.mark.trace("R25", "T-E15-030")
@pytest.mark.parametrize("query", ["kind=invalid", "kind=ack&cursor=x", "kind=ack&limit=201"])
def test_history_page_rejects_invalid_bounds(tmp_path: Path, query: str) -> None:
    s = station(tmp_path)
    ep = s.create()
    response = s.client.get("/v1/episodes/" + ep + "/history?" + query, headers={"Authorization": "patient"})
    assert response.status_code == 400, response.text


@pytest.mark.trace("R18", "D09")
def test_acting_authority_is_truthful_over_http(tmp_path: Path) -> None:
    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.facts.authority_kind = "acting_medical_officer"
    response = s.client.post(
        "/v1/episodes/" + ep + "/reviews",
        headers={
            "Authorization": "officer",
            "Idempotency-Key": "cmd_acting_http",
            "If-Match": "2",
            "Accept": "application/json",
        },
        json={
            "decisions": [{"item": "context", "decision": "not_assessed", "rationale": "acting review"}],
            "unresolved": [],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "acting_medical_officer"
    assert response.json()["authority"] == "acting_medical_officer"
    assert response.json()["role_assignment_id"] == "rol_authority"


@pytest.mark.trace("V12", "V25", "D09")
def test_visibility_never_infers_grants_from_roles_or_ground_consent() -> None:
    from dataclasses import replace

    from crew_health.episodes.domain.visibility import visibility
    from crew_health.kernel.uow import Actor

    from .builders import GROUND, OFFICER, PATIENT

    assert visibility(PATIENT, "person_a") == "full"
    assert visibility(OFFICER, "person_a") == "none"
    assert visibility(OFFICER, "person_a", object_grant=True) == "filtered"
    assert visibility(GROUND, "person_a", object_grant=True) == "none"
    assert visibility(replace(PATIENT, actor=Actor("assistant", "person_a")), "person_a") == "none"
    assert visibility(replace(PATIENT, actor=Actor("person", "someone_else")), "person_a") == "none"


@pytest.mark.trace("V12", "V25")
def test_list_checks_authenticated_identity_and_missing_grant_provider(tmp_path: Path) -> None:
    from dataclasses import replace

    from crew_health.kernel.errors import ProblemError
    from crew_health.kernel.uow import Actor

    from .builders import OFFICER, PATIENT

    s = station(tmp_path)
    s.create()
    with s.db.snapshot() as view:
        with pytest.raises(ProblemError):
            s.queries.list_episodes(view, replace(PATIENT, actor=Actor("person", "other")), None, None, 20)
        s.service.providers.authorization = None
        with pytest.raises(ProblemError):
            s.queries.list_episodes(view, OFFICER, "person_a", None, 20)
        with pytest.raises(ProblemError):
            s.queries.delivered(view, PATIENT, "epi_unknown")


@pytest.mark.trace("V04", "D08")
def test_timeline_marks_recorded_lateness_and_exposes_only_authorized_resolution_reasons(
    tmp_path: Path,
) -> None:
    from datetime import timedelta

    from crew_health.episodes.application.dto import LinkedFact

    from .builders import NOW, PATIENT

    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    s.clock.at = NOW + timedelta(minutes=6)
    with s.db.begin(actor=PATIENT.actor, correlation_id="corr_late") as uow:
        s.handlers.linked(uow, LinkedFact("evt_late", ep, "observation", "obs_late", "accepted", "SIM", NOW))
    timeline = s.client.get("/v1/episodes/" + ep + "/timeline", headers={"Authorization": "patient"}).json()
    assert timeline["items"][-1]["late"] is True
    assert timeline["items"][0]["late"] is False
    patient = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient"}).json()
    assert "symptoms_reported_resolved" in patient["resolve_reasons"]
    assert "clinician_assessed" not in patient["resolve_reasons"]
    s.facts.authority_kind = "none"
    officer = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "officer"}).json()
    assert officer["resolve_reasons"] == [] and officer["allowed_actions"] == []


@pytest.mark.trace("V04", "D09")
def test_resolution_reasons_follow_current_authority(tmp_path: Path) -> None:
    s = station(tmp_path)
    ep = s.create()
    s.answer(ep)
    response = s.client.get("/v1/episodes/" + ep, headers={"Authorization": "patient"})
    assert "symptoms_reported_resolved" in response.json()["resolve_reasons"]
    assert "clinician_assessed" not in response.json()["resolve_reasons"]
