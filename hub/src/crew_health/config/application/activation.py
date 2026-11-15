from __future__ import annotations

from collections.abc import Callable
from typing import Literal, cast

from crew_health.config.application.loader import ConfigLoader, ValidationResult
from crew_health.config.application.ports import ConfigReleaseRepository
from crew_health.config.domain.checks import parse_document
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, EventType, Role
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import CandidateRelease, ReleaseHandle, ReleaseRevision
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReadSnapshot,
    ReplicationChange,
    SessionContext,
    UnitOfWork,
)


def release_audience(release_id: str) -> Audience:
    return Audience("config.release", release_id, AudienceProjectionKey("config", "release", 1), "1")


class ReleaseActivationService:
    def __init__(
        self,
        repository: ConfigReleaseRepository,
        loader: ConfigLoader,
        ids: IdFactory,
        *,
        origin: Callable[[ReadSnapshot], Literal["LIVE", "REPLAY", "SIM"]],
    ) -> None:
        self.repository, self.loader, self.ids, self.origin = repository, loader, ids, origin

    def validate_active(self, view: ReadSnapshot) -> ReleaseHandle:
        release_id = self.repository.active_id(view)
        release = self.repository.release(view, release_id)
        missing = self.missing_documents(view)
        if missing:
            raise ConfigInvalid(
                "active release " + release_id + " predates required documents: " + ", ".join(missing)
            )
        candidate = CandidateRelease(release_id, {name: release.document(name) for name in ConfigName})
        result = self.loader.validate(candidate, origin=self.origin(view))
        self._live_example(candidate, self.origin(view))
        if not result.passed:
            raise ConfigInvalid("active configuration failed startup validation")
        return release

    def propose(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        candidate: CandidateRelease,
        *,
        note: str | None = None,
    ) -> ValidationResult:
        if Role.EDITOR not in session.roles:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "editor role required")
        validation = self.loader.validate(candidate, origin=self.origin(uow), uow=uow)
        self.repository.store(uow, candidate, validation, note=note)
        self._replicate(uow, candidate.release_id, "create")
        self._audit(uow, candidate.release_id, "config.release_proposed")
        return validation

    def revision(self, snapshot: ReadSnapshot, release_id: str) -> ReleaseRevision:
        return self.repository.revision(snapshot, release_id)

    def activate(
        self, uow: UnitOfWork, session: SessionContext, *, release_id: str, expected_etag: str
    ) -> ReleaseRevision:
        if not session.roles & {Role.EDITOR, Role.OPERATOR}:
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "configuration activation role required")
        if not expected_etag:
            raise ProblemError(ErrorCode.VERSION_REQUIRED, "configuration ETag required")
        current = self.revision(uow, release_id)
        if current.etag != expected_etag:
            raise ProblemError(ErrorCode.STALE_VERSION, "configuration ETag mismatch")
        wire = self.repository.wire(uow, release_id)
        if wire["state"] not in {"draft", "superseded"}:
            raise ProblemError(ErrorCode.INVALID_TRANSITION, "release cannot activate")
        release = self.repository.release(uow, release_id)
        candidate = CandidateRelease(release_id, {name: release.document(name) for name in ConfigName})
        validation = self.loader.validate(candidate, origin=self.origin(uow), uow=uow)
        self._live_example(candidate, self.origin(uow))
        if not validation.passed:
            raise ConfigInvalid("activation validation failed")
        return self._activate(uow, release_id)

    def install(
        self,
        uow: UnitOfWork,
        candidate: CandidateRelease,
        *,
        note: str,
        checked: ValidationResult | None = None,
    ) -> ReleaseRevision:
        """First start (SYSTEM 5.12 CONFIG): the engine proposes and activates the hub's first release in one
        transaction, with the same validation, audit, outbox and replication as an editor's activation. Only a hub
        with no active release accepts it, and a candidate that fails any check is refused with nothing stored."""
        if uow.actor.kind != "engine":
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "first start release is engine only")
        if self.has_active(uow):
            raise ProblemError(ErrorCode.INVALID_TRANSITION, "a release is already active")
        return self._install(uow, candidate, note, checked)

    def missing_documents(self, view: ReadSnapshot) -> tuple[str, ...]:
        """Documents the active release does not pin, e.g. unit-mapping in a release from before D12."""
        release = self.repository.release(view, self.repository.active_id(view))
        missing = []
        for name in ConfigName:
            try:
                release.document(name)
            except ConfigInvalid:
                missing.append(name.value)
        return tuple(missing)

    def upgrade(self, uow: UnitOfWork, additions: CandidateRelease, *, note: str) -> ReleaseRevision:
        """Upgrade an active release that predates a required document (D12 added unit-mapping): a new release
        keeps every document the active one pins and takes only the missing ones from `additions`, validated and
        activated like any release. Nothing the crew activated is replaced."""
        if uow.actor.kind != "engine":
            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "release upgrade is engine only")
        missing = self.missing_documents(uow)
        if not missing:
            raise ProblemError(ErrorCode.INVALID_TRANSITION, "active release is complete")
        release = self.repository.release(uow, self.repository.active_id(uow))
        documents = {
            name: additions.documents[name] if name.value in missing else release.document(name)
            for name in ConfigName
        }
        return self._install(uow, CandidateRelease(additions.release_id, documents), note)

    def check(self, view: ReadSnapshot, candidate: CandidateRelease) -> ValidationResult:
        """The candidate's document checks, without the activation guards: `install(checked=...)` completes them
        inside its transaction."""
        return self.loader.validate(candidate, origin=self.origin(view))

    def _install(
        self, uow: UnitOfWork, candidate: CandidateRelease, note: str, checked: ValidationResult | None = None
    ) -> ReleaseRevision:
        validation = (
            self.loader.validate(candidate, origin=self.origin(uow), uow=uow)
            if checked is None
            else self.loader.guarded(candidate, checked, uow)
        )
        self._live_example(candidate, self.origin(uow))
        if not validation.passed:
            raise ConfigInvalid("engine release failed validation")
        self.repository.store(uow, candidate, validation, note=note)
        self._replicate(uow, candidate.release_id, "create")
        self._audit(uow, candidate.release_id, "config.release_proposed")
        return self._activate(uow, candidate.release_id)

    def has_active(self, view: ReadSnapshot) -> bool:
        try:
            self.repository.active_id(view)
        except ConfigInvalid:
            return False
        return True

    def _activate(self, uow: UnitOfWork, release_id: str) -> ReleaseRevision:
        self._swap(uow, release_id)
        self._replicate(uow, release_id, "version")
        self._audit(uow, release_id, "config.release_activated")
        uow.emit(
            OutboxEvent(
                EventType.CONFIG_RELEASE_ACTIVATED,
                uow.now,
                EventScope("crew", None, None),
                release_id,
                uow.correlation_id,
                canonical_json(self.repository.wire(uow, release_id)),
                release_audience(release_id),
            )
        )
        return self.revision(uow, release_id)

    def _swap(self, uow: UnitOfWork, release_id: str) -> None:
        rows = uow.connection.rows("SELECT release_id FROM config_activation WHERE singleton=1", (), limit=1)
        old_id = rows[0][0] if rows else None
        if isinstance(old_id, str):
            uow.connection.execute(
                "UPDATE config_release SET state='superseded',superseded_at=?,object_revision=object_revision+1 WHERE release_id=?",
                (instant(uow), old_id),
            )
            self._replicate(uow, old_id, "version")
        actor = canonical_json({"kind": uow.actor.kind, "id": uow.actor.id, "display_name": None})
        uow.connection.execute(
            "UPDATE config_release SET state='active',activated_at=?,activated_by_json=?,superseded_at=NULL,object_revision=object_revision+1 WHERE release_id=?",
            (instant(uow), actor, release_id),
        )
        changed = uow.connection.execute(
            "UPDATE config_activation SET release_id=?,generation=generation+1 WHERE singleton=1",
            (release_id,),
        )
        if changed.affected_rows != 1:
            raise ConfigInvalid("activation pointer absent")

    @staticmethod
    def _live_example(candidate: CandidateRelease, origin: str) -> None:
        if origin != "LIVE":
            return
        document = candidate.documents.get(ConfigName.KIT_MAP)
        if document is None:
            raise ConfigInvalid("kit map unavailable")
        value = parse_document(document.raw)
        if not isinstance(value, dict) or cast(dict[str, object], value).get("example") is True:
            raise ConfigInvalid("example kit forbidden in LIVE")

    def _audit(self, uow: UnitOfWork, release_id: str, event_type: str) -> None:
        uow.audit(
            AuditEntry(
                self.ids.new("audit"),
                event_type,
                1,
                uow.actor,
                {"release_id": release_id},
                uow.correlation_id,
                None,
                {},
            )
        )

    def _replicate(self, uow: UnitOfWork, release_id: str, operation: Literal["create", "version"]) -> None:
        uow.replicate(
            ReplicationChange(
                None,
                "config.release",
                release_id,
                operation,
                canonical_json(self.repository.wire(uow, release_id)),
                (),
                release_id,
                release_audience(release_id),
            )
        )


def instant(view: ReadSnapshot) -> str:
    return view.now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
