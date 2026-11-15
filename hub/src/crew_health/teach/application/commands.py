"""Use cases startGuidedCheck, getGuidedCheck, tapBeat and actOnGuidedCheck (E30 4.2, 4.3).

Every command: authenticate (done by the API with E02's gate), idempotency lookup by operation, actor, target and
body, load, object authorization, If-Match where required, the domain command, effects, response. One command is
one unit of work holding the state change, the audit entry, the outbox events and the idempotency result; no unit of
work is held across a camera call. A failure writes nothing, except the two beat refusals that mark the attempt
incomplete (3.3)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import (
    CommandIdentity,
    CommandReceipts,
    IdempotencyStore,
    StoredResult,
    canonical_json,
    command_identity,
)
from crew_health.kernel.config import ConfigBinding, ConfigReleaseReader
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.units import UnitRegistry
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReadSnapshotFactory,
    ReplicationChange,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.teach.application import access
from crew_health.teach.application.content import ContentLoader, PinnedContent
from crew_health.teach.application.errors import problem_of
from crew_health.teach.application.ports import (
    AnswerCommands,
    CameraReferencePort,
    GuidanceEligibility,
    GuidedCheckRepository,
    ObservationCommands,
    PersonDirectory,
    PlanRunReader,
    RecordRouting,
    RoleAssignmentReader,
)
from crew_health.teach.application.queries import display_names, project, project_steps
from crew_health.teach.application.settings import TeachSettings
from crew_health.teach.domain.capture import ClockReading
from crew_health.teach.domain.errors import TeachError
from crew_health.teach.domain.guided_check import GuidedCheck

AUDIENCE_RULE = "teach.guided_check_parties"
AUDIENCE_PROJECTION = AudienceProjectionKey("teach", "guided-check", 1)
POLICY_VERSION = "teach-access-1"


@dataclass(frozen=True, slots=True)
class TeachPorts:
    uow: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    clock: Clock
    ids: IdFactory
    settings: TeachSettings
    releases: ConfigReleaseReader
    content: ContentLoader
    config: ConfigBinding
    idempotency: IdempotencyStore
    receipts: CommandReceipts
    units: UnitRegistry
    repository: GuidedCheckRepository
    observations: ObservationCommands
    answers: AnswerCommands
    eligibility: GuidanceEligibility
    people: PersonDirectory
    routing: RecordRouting
    plans: PlanRunReader | None
    camera: CameraReferencePort | None
    roles: RoleAssignmentReader | None


@dataclass(frozen=True, slots=True)
class CommandMeta:
    session: SessionContext
    idempotency_key: str
    correlation_id: str | None
    body: bytes


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: dict[str, object]
    revision: int
    release_id: str  # the release the check pinned, sent as Config-Release


def not_found() -> ProblemError:
    return ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)


class TeachCore:
    """Shared mechanics of the teach use cases: identity, units of work, loading, projection, audit, events."""

    def __init__(self, ports: TeachPorts) -> None:
        self.p = ports

    def reading(self) -> ClockReading:
        clock = self.p.clock
        return ClockReading(clock.now(), clock.monotonic_ns(), clock.process_epoch())

    def identity(self, meta: CommandMeta, operation: str, target: str) -> CommandIdentity:
        return command_identity(
            key=meta.idempotency_key,
            actor_ref=meta.session.actor.id,
            operation=operation,
            target=target,
            parameters_json=meta.body,
            body_fingerprint=hashlib.sha256(meta.body).hexdigest(),
            fingerprint_kind="sha256",
        )

    @contextmanager
    def unit(self, meta: CommandMeta, identity: CommandIdentity | None) -> Iterator[UnitOfWork]:
        correlation = meta.correlation_id or self.p.ids.new("cor")
        with self.p.uow.begin(actor=meta.session.actor, command=identity, correlation_id=correlation) as uow:
            yield uow

    def load(self, view: UnitOfWork, guided_check_id: str) -> GuidedCheck:
        check = self.p.repository.get(view, guided_check_id)
        if check is None:
            raise not_found()
        return check

    def content_of(self, check: GuidedCheck) -> PinnedContent:
        return self.p.content.load(check.pins)

    def response(
        self, check: GuidedCheck, session: SessionContext, status: int, r: ClockReading | None = None
    ) -> Response:
        reading = r or self.reading()
        content = self.content_of(check)
        skill = content.skill(check.skill_id)
        allowed = [a for a in check.allowed_actions(skill, reading) if self._may(session, check, a)]
        body = project(check, content, display_names(self.p.people, check), reading, allowed=allowed)
        return Response(status, body, check.revision, check.config_release_id)

    def steps(self, view: UnitOfWork, check: GuidedCheck) -> dict[str, object]:
        names = display_names(self.p.people, check)
        return project_steps(check, self.content_of(check), self.p.config.bind(view), names)

    @staticmethod
    def _may(session: SessionContext, check: GuidedCheck, action: str) -> bool:
        return access.allows(session, check, "cancel" if action == "cancel" else "act")

    def replay(self, uow: UnitOfWork, identity: CommandIdentity, session: SessionContext) -> Response | None:
        stored = self.p.idempotency.lookup(uow, identity)
        if stored is None:
            return None
        if stored.resource_ref is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        check = self.load(uow, stored.resource_ref)
        access.authorize(session, check, "read")
        return self.response(check, session, stored.status)

    def remember(self, uow: UnitOfWork, identity: CommandIdentity, check: GuidedCheck, status: int) -> None:
        receipt = self.p.receipts.accepted(uow, identity)
        result = StoredResult(
            receipt, status, check.guided_check_id, "accepted", None
        )  # SYSTEM 5.8 ledger code
        self.p.idempotency.record(uow, identity, result)

    def register(self, uow: UnitOfWork, check: GuidedCheck) -> None:
        """SYSTEM 5.7: register the new check's local record reference atomically, so the reference-only audit
        entries that name it resolve (as plans does for its runs). No export projection exists for checks."""
        uow.replicate(
            ReplicationChange(
                None,
                "guided_check",
                check.guided_check_id,
                "create",
                canonical_json({"guided_check_id": check.guided_check_id}),
                (),
                uow.release.release_id,
                Audience(AUDIENCE_RULE, check.guided_check_id, AUDIENCE_PROJECTION, POLICY_VERSION),
            )
        )

    def save(self, uow: UnitOfWork, check: GuidedCheck) -> None:
        expected = check.revision
        check.revision += 1
        self.p.repository.save(uow, check, expected_revision=expected)
        if check.attempt is not None:
            check.attempt.new_beats.clear()

    def audit(self, uow: UnitOfWork, session: SessionContext, event: str, check: GuidedCheck) -> None:
        refs = {"guided_check_id": check.guided_check_id, "session_id": session.session_id}
        # SYSTEM 5.8: enumerated values only (module.audit_policies); the revision is on the check itself.
        metadata: dict[str, str | int | bool] = {"state": check.state}
        if check.result is not None:
            metadata["result"] = check.result
        uow.audit(
            AuditEntry(
                event_id=self.p.ids.new("aud"),
                event_type="teach." + event,
                event_version=1,
                actor=session.actor,
                refs=refs,
                correlation_id=uow.correlation_id,
                causation_id=None,
                safe_metadata=metadata,
            )
        )

    def emit(
        self, uow: UnitOfWork, check: GuidedCheck, kind: EventType, data: Mapping[str, object], at: datetime
    ) -> None:
        """One person-scoped event per party: the helper and the person whose record holds the count (4.6)."""
        audience = Audience(AUDIENCE_RULE, check.guided_check_id, AUDIENCE_PROJECTION, POLICY_VERSION)
        payload = canonical_json(dict(data))
        for person in sorted({check.helper_id, check.measured_record_id}):
            uow.emit(
                OutboxEvent(
                    type=kind,
                    occurred_at=at,
                    scope=EventScope("person", person, None),
                    config_release_id=check.config_release_id,
                    correlation_id=uow.correlation_id,
                    data_json=payload,
                    audience=audience,
                )
            )

    def if_match(self, check: GuidedCheck, header: str | None, session: SessionContext) -> None:
        if header is None:
            raise ProblemError(ErrorCode.VERSION_REQUIRED, ErrorCode.VERSION_REQUIRED.value)
        if header.strip().strip('"') != str(check.revision):
            current = self.response(check, session, 200).body
            raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value, current_state=current)

    def refuse(
        self, uow: UnitOfWork, guided_check_id: str, session: SessionContext, error: TeachError
    ) -> ProblemError:
        """A refused command: the mapped error, with the stored check for INVALID_TRANSITION (error.schema)."""
        stored = self.load(uow, guided_check_id)
        return problem_of(error, current_state=self.response(stored, session, 200).body)

    @staticmethod
    def ms(value: int) -> timedelta:
        return timedelta(milliseconds=value)
