from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from crew_health.contracts_gen.enums import Role
from crew_health.episodes.adapters.sqlite_repo import EpisodeRepository
from crew_health.episodes.api.router import EpisodeApi, build_router
from crew_health.episodes.application.dto import Answer, CrewMember, NewEpisode, PersonRef
from crew_health.episodes.application.handlers import EpisodeHandlers
from crew_health.episodes.application.queries import EpisodeQueries
from crew_health.episodes.application.rules import EpisodeRulesSource, parse_rules
from crew_health.episodes.application.use_cases import EpisodeService, Limits, Providers
from crew_health.episodes.domain.onset import Onset
from crew_health.episodes.module import setting_defaults
from crew_health.kernel.commands import CommandIdentity, canonical_json, command_identity
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.testing import MemoryClock
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database
from crew_health.roles.application.ports import AuthorityDecision, DoseAuthorityContext

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)
PATIENT = SessionContext(
    "session_patient", "crew", Actor("person", "person_a"), "person_a", frozenset({Role.CREW})
)
OFFICER = SessionContext(
    "session_officer",
    "crew",
    Actor("person", "officer_a"),
    "officer_a",
    frozenset({Role.CREW_MEDICAL_OFFICER}),
)
OTHER = SessionContext(
    "session_other", "crew", Actor("person", "person_b"), "person_b", frozenset({Role.CREW})
)
GROUND = SessionContext(
    "session_ground", "ground", Actor("ground", "ground"), "ground", frozenset({Role.GROUND})
)

type View = ReadSnapshot | UnitOfWork


@dataclass
class Facts:
    rows: list[Answer] = field(default_factory=list)
    urgency: str = "none"
    consent: bool = False
    shared: bool = True
    authority_kind: Literal["crew_medical_officer", "acting_medical_officer", "none"] = "crew_medical_officer"

    def require_read(
        self, view: View, session: SessionContext, *, object_ref: str, patient_id: str, fields: frozenset[str]
    ) -> None:
        if not self.shared or session.person_id not in ("officer_a", "ground"):
            raise ProblemError(ErrorCode.NOT_FOUND, "not shared")

    def authority(
        self,
        snapshot: View,
        *,
        person_id: str,
        subject_person_id: str,
        action: Literal["urgency_disposition", "dose_confirmation", "record_review", "clinician_assessed"],
        dose: DoseAuthorityContext | None,
        at: datetime,
    ) -> AuthorityDecision:
        kind = self.authority_kind if person_id == "officer_a" and person_id != subject_person_id else "none"
        if action == "clinician_assessed" and kind == "acting_medical_officer":
            kind = "none"
        return AuthorityDecision(kind, "rol_authority" if kind != "none" else None, "test assignment")

    def for_episode(self, uow: View, episode_id: str) -> Sequence[Answer]:
        return tuple(a for a in self.rows if a.episode_id == episode_id)

    def question_ids_answered(self, uow: UnitOfWork, episode_id: str) -> frozenset[str]:
        return frozenset(a.question_id for a in self.for_episode(uow, episode_id))

    def heads(self, uow: UnitOfWork, episode_id: str) -> Mapping[str, Answer]:
        return {a.question_id: a for a in self.for_episode(uow, episode_id)}

    def summary(self, uow: View, episode_id: str) -> str:
        return self.urgency

    def blocks_resolution(self, uow: UnitOfWork, episode_id: str) -> bool:
        return self.urgency in ("needs_urgent_review", "active_response")

    def crew(self, at: datetime) -> Sequence[CrewMember]:
        return (
            CrewMember("person_a", "A", None),
            CrewMember("person_b", "B", None),
            CrewMember("officer_a", "Officer", 1),
            CrewMember("officer_b", "Second", 2),
        )

    def allows(self, uow: View, subject_id: str, recipient_id: str, scope: tuple[str, str]) -> bool:
        return self.consent

    def is_restricted(self, questionnaire_version: str, question_id: str) -> bool:
        return question_id == "private_question"

    def origin(self, uow: View, patient_id: str, mission_id: str) -> str:
        return "SIM"


class Auth:
    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext | None:
        return {"patient": PATIENT, "officer": OFFICER, "other": OTHER, "ground": GROUND}.get(
            headers.get("authorization", "")
        )


class Fingerprint:
    def fingerprint(self, canonical_body: bytes) -> str:
        return hmac.new(b"explicit-test-key", canonical_body, hashlib.sha256).hexdigest()


@dataclass
class Station:
    db: SQLiteDatabase
    service: EpisodeService
    queries: EpisodeQueries
    handlers: EpisodeHandlers
    facts: Facts
    client: TestClient
    clock: MemoryClock

    def create(self, *, red_flags: tuple[str, ...] = ("red_one",)) -> str:
        with self.db.begin(actor=PATIENT.actor, correlation_id="corr_create") as uow:
            draft = NewEpisode(
                patient_id="person_a",
                mission_id="mission_one",
                origin_mode="SIM",
                concern_text="private-concern-canary",
                onset=Onset(None, "unknown"),
                created_by=PersonRef("person_a", "A"),
                questionnaire_version="0" * 64,
                red_flag_ids=red_flags,
                config_release_id="release_1",
                received_at=NOW,
            )
            return self.service.create(uow, draft).episode_id

    def answer(
        self, ep: str, question: str = "red_one", presence: str = "absent", *, supersedes: str | None = None
    ) -> str:
        from crew_health.episodes.application.dto import AnswerFact

        ref = self.db.ids.new("ans")
        self.facts.rows.append(
            Answer(ref, question, "0" * 64, presence, "person_a", ep, None, NOW, "SIM", supersedes)
        )
        with self.db.begin(actor=PATIENT.actor, correlation_id="corr_answer") as uow:
            self.handlers.answer(uow, AnswerFact(ep, ref, question, "SIM", NOW, supersedes))
        return ref

    def identity(
        self,
        key: str,
        body: Mapping[str, object],
        ep: str,
        operation: str = "actOnEpisode",
        session: SessionContext = PATIENT,
    ) -> CommandIdentity:
        return command_identity(
            key=key,
            actor_ref=session.actor.id,
            operation=operation,
            target=ep,
            parameters_json=b"{}",
            body_fingerprint=Fingerprint().fingerprint(canonical_json(dict(body))),
            fingerprint_kind="hmac_sha256",
        )

    def act(
        self,
        ep: str,
        body: Mapping[str, object],
        *,
        session: SessionContext = PATIENT,
        key: str | None = None,
        expected: int | None = None,
    ) -> int:
        with self.db.begin(actor=session.actor, correlation_id="corr_action") as uow:
            revision = self.service.repo.require(uow, ep).revision if expected is None else expected
            result = self.service.act(
                uow,
                session,
                ep,
                body,
                revision,
                self.identity(key or self.db.ids.new("cmd"), body, ep, session=session),
            )
            return result.revision


def station(path: Path) -> Station:
    clock = MemoryClock(NOW, 0, "epoch_one")
    db = database(path, clock=clock)
    db.migrate(
        (ROOT / "hub/src/crew_health/episodes/adapters/migrations/0001_episodes.sql").read_text(
            encoding="utf-8"
        )
    )
    from crew_health.episodes.module import audit_policies

    for policy in audit_policies():
        db.register_audit_policy(policy)
    f = Facts()
    values = setting_defaults()
    limits = Limits(**{k.split(".", 1)[1]: v for k, v in values.items()})
    rules = EpisodeRulesSource(parse_rules((ROOT / "config/episode-rules.json").read_bytes()), "release_1")
    service = EpisodeService(
        EpisodeRepository(max_links=limits.max_links_per_episode, max_tasks=limits.max_open_tasks),
        db.ids,
        rules,
        Providers(
            authorization=f,
            clinical_authority=f,
            answers=f,
            urgency=f,
            roster=f,
            consent=f,
            sensitivity=f,
            origin=f,
        ),
        limits,
        CommandLedger(db.ids),
    )
    queries = EpisodeQueries(service, db)
    handlers = EpisodeHandlers(service)
    app = FastAPI()
    app.include_router(build_router(EpisodeApi(service, queries, db, db, Auth(), Fingerprint(), db.ids)))
    return Station(
        db,
        service,
        queries,
        handlers,
        f,
        TestClient(app, headers={"Accept": "application/vnd.crew-health.episode+json"}),
        clock,
    )
