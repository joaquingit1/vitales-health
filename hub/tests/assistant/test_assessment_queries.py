from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from crew_health.assistant.application.access import Access
from crew_health.assistant.application.assessment_queries import AssessmentQueries
from crew_health.assistant.application.ports import DeliveredAssessment
from crew_health.assistant.application.state_ports import View
from crew_health.contracts_gen.enums import Role
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.platform.storage import SQLiteDatabase

from .test_access import OWNER, Grants, Sessions
from .test_store import ACTOR, AT, STORE, assessment, db

assert db

GROUND = SessionContext(
    "session_ground", "ground", Actor("ground", "ground_own"), None, frozenset({Role.GROUND})
)


def body() -> dict[str, object]:
    return {
        "assessment_id": "assessment_own",
        "episode_id": "episode_own",
        "origin": "model",
        "state_snapshot_version": 7,
        "evidence_release_id": "evidence_A",
        "model_release_id": "model_A",
        "findings": [],
        "hypotheses": [],
        "contradictions": [],
        "missing_evidence": [
            {"text": "Configured incomplete fixture words", "body_region": None, "evidence": []}
        ],
        "next_step": None,
        "validation_status": "draft",
        "created_at": AT.isoformat().replace("+00:00", "Z"),
    }


def delivered() -> DeliveredAssessment:
    raw = canonical_json(body())
    return DeliveredAssessment(
        "handoff_own",
        "delivery_own",
        "assessment_own",
        "episode_own",
        "person_own",
        "ground_own",
        frozenset(body()),
        hashlib.sha256(raw).hexdigest(),
        raw,
        "config_A",
        "evidence_A",
        "model_A",
        1,
        AT,
        "SIM",
        AT,
        "draft",
    )


class Delivered:
    def __init__(self, value: DeliveredAssessment | None) -> None:
        self.value = value
        self.calls: list[tuple[SessionContext, str, frozenset[str]]] = []

    def read(
        self, view: View, session: SessionContext, *, assessment_id: str, fields: frozenset[str]
    ) -> DeliveredAssessment | None:
        self.calls.append((session, assessment_id, fields))
        return self.value


@pytest.mark.trace("T-E13-040", "T-E13-050", "SYSTEM.8.3", "SYSTEM.8.7")
def test_ground_receives_only_actual_immutable_delivered_bytes_with_no_live_assessment(
    db: SQLiteDatabase,
) -> None:
    provider = Delivered(delivered())
    queries = AssessmentQueries(STORE, Access(Sessions(), Grants(True)), delivered=provider, max_bytes=8192)
    with db.snapshot() as view:
        assert STORE.assessment(view, "assessment_own") is None
        before = view.connection.rows("SELECT count(*) FROM platform_audit", (), limit=1)
        assert queries.get(view, GROUND, "assessment_own") == body()
        assert provider.calls == [(GROUND, "assessment_own", frozenset(body()))]
        assert view.connection.rows("SELECT count(*) FROM platform_audit", (), limit=1) == before


@pytest.mark.trace("T-E13-050", "SYSTEM.8.7")
def test_delivered_wire_values_are_not_reformatted_after_signature_verification(db: SQLiteDatabase) -> None:
    original = {**body(), "created_at": "2026-11-15T00:00:00.000Z"}
    raw = canonical_json(original)
    source = replace(delivered(), body_json=raw, content_sha256=hashlib.sha256(raw).hexdigest())
    queries = AssessmentQueries(
        STORE, Access(Sessions(), Grants(True)), delivered=Delivered(source), max_bytes=8192
    )
    with db.snapshot() as view:
        assert canonical_json(queries.get(view, GROUND, "assessment_own")) == raw


@pytest.mark.trace("T-E13-043", "V25", "SYSTEM.8.3", "SYSTEM.8.7")
@pytest.mark.parametrize(
    "change",
    [
        {"recipient_id": "ground_foreign"},
        {"assessment_id": "assessment_foreign"},
        {"episode_id": "episode_foreign"},
        {"evidence_release_id": "evidence_B"},
        {"model_release_id": "model_B"},
        {"selected_fields": frozenset({"findings"})},
        {"content_sha256": "0" * 64},
        {"body_json": canonical_json({**body(), "extra_private_text": "private canary"})},
        {"source_revision": 0},
    ],
)
def test_delivered_projection_tampering_is_refused_without_live_enrichment(
    db: SQLiteDatabase, change: dict[str, object]
) -> None:
    provider = Delivered(replace(delivered(), **change))  # type: ignore[arg-type]
    queries = AssessmentQueries(STORE, Access(Sessions(), Grants(True)), delivered=provider, max_bytes=8192)
    with db.snapshot() as view:
        with pytest.raises(ProblemError):
            queries.get(view, GROUND, "assessment_own")


@pytest.mark.trace("T-E13-043", "V25", "SYSTEM.8.3", "SYSTEM.8.7")
def test_ground_revocation_and_missing_bridge_cannot_use_local_live_record(db: SQLiteDatabase) -> None:
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        STORE.create_assessment(uow, replace(assessment(), body=canonical_json(body())), ())
    sessions = Sessions()
    provider = Delivered(None)
    queries = AssessmentQueries(STORE, Access(sessions, Grants(True)), delivered=provider, max_bytes=8192)
    with db.snapshot() as view:
        with pytest.raises(ProblemError) as error:
            queries.get(view, GROUND, "assessment_own")
        assert error.value.code == ErrorCode.NOT_FOUND
        queries.delivered = None
        with pytest.raises(ProblemError) as unavailable:
            queries.get(view, GROUND, "assessment_own")
        assert unavailable.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
        sessions.live = False
        queries.delivered = Delivered(delivered())
        with pytest.raises(ProblemError):
            queries.get(view, GROUND, "assessment_own")


@pytest.mark.trace("T-E13-033", "T-E13-040", "T-E13-080", "SYSTEM.8.3", "SYSTEM.8.6")
def test_selected_owner_fields_have_exact_body_hash_pins_and_status_revision(db: SQLiteDatabase) -> None:
    queries = AssessmentQueries(STORE, Access(Sessions(), Grants(True)), delivered=None, max_bytes=8192)
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        STORE.create_assessment(uow, replace(assessment(), body=canonical_json(body())), ())
        STORE.mark_assessment(uow, "assessment_own", state="reviewed", review="review_own")
        STORE.mark_assessment(uow, "assessment_own", state="stale", cause="correction_own")
    with db.snapshot() as view:
        selected = queries.read(
            view, OWNER, assessment_id="assessment_own", fields=frozenset({"findings", "missing_evidence"})
        )
        assert json.loads(selected.body_json) == {
            "findings": [],
            "missing_evidence": body()["missing_evidence"],
        }
        assert selected.source_revision == 3 and selected.validation_status == "stale"
        assert selected.origin_mode == "SIM" and selected.created_at == AT
        assert (
            selected.config_release_id == "config_A"
            and selected.evidence_release_id == "evidence_A"
            and selected.model_release_id == "model_A"
        )
        assert selected.subject_person_id == "person_own" and selected.assessment_id == "assessment_own"
        assert selected.selected_fields == frozenset({"findings", "missing_evidence"})
        assert hashlib.sha256(selected.body_json).hexdigest() == selected.content_sha256
        assert queries.get(view, OWNER, "assessment_own")["validation_status"] == "stale"


@pytest.mark.trace("T-E13-043", "V25", "SYSTEM.8.3")
def test_selection_checks_exact_field_grants_and_cannot_select_unknown_private_fields(
    db: SQLiteDatabase,
) -> None:
    grants = Grants(True)
    queries = AssessmentQueries(STORE, Access(Sessions(), grants), delivered=None, max_bytes=8192)
    officer = replace(OWNER, roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        STORE.create_assessment(
            uow, replace(assessment(), person_id="person_foreign", body=canonical_json(body())), ()
        )
    with db.snapshot() as view:
        queries.read(view, officer, assessment_id="assessment_own", fields=frozenset({"findings"}))
        assert "findings" in grants.calls[0][3] and "hypotheses" not in grants.calls[0][3]
        for fields in (frozenset(), frozenset({"journal"})):
            with pytest.raises(ProblemError):
                queries.read(view, officer, assessment_id="assessment_own", fields=fields)
        grants.allowed = False
        with pytest.raises(ProblemError) as error:
            queries.read(view, officer, assessment_id="assessment_own", fields=frozenset({"findings"}))
        assert error.value.code == ErrorCode.NOT_FOUND and error.value.current_state is None


@pytest.mark.trace("T-E13-043", "T-E13-092", "V25", "SYSTEM.8.7")
@pytest.mark.parametrize(
    "field,value",
    [
        ("state_snapshot_version", True),
        ("state_snapshot_version", "7"),
        ("created_at", "2026-11-15T00:00:00+00:00"),
        ("created_at", "2026-11-15T00:00:00"),
        ("extra_private_text", "private canary"),
    ],
)
def test_delivered_body_is_schema_checked_even_when_hash_and_identity_match(
    db: SQLiteDatabase, field: str, value: object
) -> None:
    raw = canonical_json({**body(), field: value})
    source = replace(delivered(), body_json=raw, content_sha256=hashlib.sha256(raw).hexdigest())
    queries = AssessmentQueries(
        STORE, Access(Sessions(), Grants(True)), delivered=Delivered(source), max_bytes=8192
    )
    with db.snapshot() as view:
        with pytest.raises(ProblemError):
            queries.get(view, GROUND, "assessment_own")
