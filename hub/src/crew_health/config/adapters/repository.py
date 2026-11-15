from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from crew_health.config.application.loader import ValidationResult
from crew_health.config.domain.checks import parse_document
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, ReleaseHandle, ReleaseRevision
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.ids import require_id
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork


def text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise StorageUnavailable("corrupt")
    return value


def integer(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise StorageUnavailable("corrupt")
    return value


def blob(value: SqlValue) -> bytes:
    if not isinstance(value, bytes):
        raise StorageUnavailable("corrupt")
    return value


def instant(view: ReadSnapshot) -> str:
    return view.now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class PinnedRelease:
    release_id: str
    documents: Mapping[ConfigName, ConfigDocumentView]

    def __post_init__(self) -> None:
        require_id(self.release_id)
        for name, document in self.documents.items():
            if name != document.name or hashlib.sha256(document.raw).hexdigest() != document.version:
                raise ConfigInvalid("document identity or hash mismatch")
        object.__setattr__(self, "documents", MappingProxyType(dict(self.documents)))

    def document(self, name: ConfigName) -> ConfigDocumentView:
        try:
            return self.documents[name]
        except KeyError as exc:
            raise ConfigInvalid("required pinned document unavailable") from exc

    def version_of(self, name: ConfigName) -> str:
        return self.document(name).version


class ConfigRepository:
    def release(self, view: ReadSnapshot, release_id: str) -> PinnedRelease:
        rows = view.connection.rows(
            "SELECT p.name,p.version,d.raw FROM config_release_pin p JOIN config_document d ON d.name=p.name AND d.version=p.version WHERE p.release_id=? ORDER BY p.name",
            (require_id(release_id),),
            limit=len(ConfigName),
        )
        if not rows:
            raise ProblemError(ErrorCode.NOT_FOUND, "configuration release absent")
        documents = {}
        for name, version, raw in rows:
            try:
                key = ConfigName(text(name))
            except ValueError as exc:
                raise StorageUnavailable("corrupt") from exc
            documents[key] = ConfigDocumentView(key, text(version), blob(raw))
        return PinnedRelease(release_id, documents)

    def active_id(self, view: ReadSnapshot) -> str:
        rows = view.connection.rows("SELECT release_id FROM config_activation WHERE singleton=1", (), limit=1)
        if not rows or rows[0][0] is None:
            raise ConfigInvalid("no validated active release")
        return text(rows[0][0])

    def loaded_at(self, view: ReadSnapshot, name: ConfigName, version: str) -> str:
        rows = view.connection.rows(
            "SELECT loaded_at FROM config_document WHERE name=? AND version=?", (name.value, version), limit=1
        )
        if not rows:
            raise ProblemError(ErrorCode.NOT_FOUND, "configuration document absent")
        return text(rows[0][0])

    def generation(self, view: ReadSnapshot) -> int:
        rows = view.connection.rows("SELECT generation FROM config_activation WHERE singleton=1", (), limit=1)
        if not rows:
            raise StorageUnavailable("corrupt")
        return integer(rows[0][0])

    def revision(self, view: ReadSnapshot, release_id: str) -> ReleaseRevision:
        rows = view.connection.rows(
            "SELECT object_revision FROM config_release WHERE release_id=?", (release_id,), limit=1
        )
        if not rows:
            raise ProblemError(ErrorCode.NOT_FOUND, "configuration release absent")
        revision = integer(rows[0][0])
        return ReleaseRevision(release_id, revision, f'"{release_id}:{revision}:{self.generation(view)}"')

    def store(
        self, uow: UnitOfWork, candidate: CandidateRelease, validation: ValidationResult, *, note: str | None
    ) -> None:
        if not candidate.documents:
            raise ConfigInvalid("release has no documents")
        body = {
            "passed": validation.passed,
            "errors": self._findings(validation.errors),
            "warnings": self._findings(validation.warnings),
        }
        uow.connection.execute(
            "INSERT INTO config_release VALUES (?,?,?,?,NULL,NULL,NULL,?,?,1)",
            (
                candidate.release_id,
                "draft" if validation.passed else "rejected",
                instant(uow),
                canonical_json({"kind": uow.actor.kind, "id": uow.actor.id, "display_name": None}),
                canonical_json(body),
                note,
            ),
        )
        for name, document in sorted(candidate.documents.items()):
            uow.connection.execute(
                "INSERT OR IGNORE INTO config_document VALUES (?,?,?,?)",
                (name.value, document.version, document.raw, instant(uow)),
            )
            stored = uow.connection.rows(
                "SELECT raw FROM config_document WHERE name=? AND version=?",
                (name.value, document.version),
                limit=1,
            )
            if not stored or blob(stored[0][0]) != document.raw:
                raise StorageUnavailable("corrupt")
            uow.connection.execute(
                "INSERT INTO config_release_pin VALUES (?,?,?)",
                (candidate.release_id, name.value, document.version),
            )

    @staticmethod
    def _findings(findings: tuple[object, ...]) -> list[dict[str, str]]:
        from crew_health.kernel.config import Finding

        result = []
        for finding in findings:
            if not isinstance(finding, Finding):
                raise ConfigInvalid()
            # A missing contributor is release-wide. The closed base envelope requires a document.
            for name in (finding.document,) if finding.document is not None else tuple(ConfigName):
                result.append(
                    {"document": name.value, "pointer": finding.pointer, "message": finding.message}
                )
        return result

    def wire(self, view: ReadSnapshot, release_id: str) -> dict[str, object]:
        rows = view.connection.rows(
            "SELECT state,created_at,created_by_json,activated_at,activated_by_json,superseded_at,validation_json,note FROM config_release WHERE release_id=?",
            (release_id,),
            limit=1,
        )
        if not rows:
            raise ProblemError(ErrorCode.NOT_FOUND, "configuration release absent")
        state, created_at, created_by, activated_at, activated_by, superseded_at, validation, note = rows[0]
        pins = self.release(view, release_id).documents
        return {
            "release_id": release_id,
            "state": text(state),
            "pins": [
                {"name": name.value, "version": document.version} for name, document in sorted(pins.items())
            ],
            "created_at": text(created_at),
            "created_by": parse_document(blob(created_by)),
            "activated_at": activated_at,
            "activated_by": parse_document(blob(activated_by)) if activated_by is not None else None,
            "superseded_at": superseded_at,
            "validation": parse_document(blob(validation)),
            "note": note,
        }

    def list_ids(self, view: ReadSnapshot, *, after: str | None, limit: int) -> tuple[str, ...]:
        if not 1 <= limit <= 201:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, "invalid page limit")
        rows = view.connection.rows(
            "SELECT release_id FROM config_release WHERE release_id>? ORDER BY release_id LIMIT ?",
            (after or "", limit),
            limit=limit,
        )
        return tuple(text(row[0]) for row in rows)


@dataclass(frozen=True, slots=True)
class SQLiteConfigReader:
    view: ReadSnapshot
    repository: ConfigRepository

    def active(self) -> ReleaseHandle:
        return self.release(self.repository.active_id(self.view))

    def release(self, release_id: str) -> ReleaseHandle:
        return self.repository.release(self.view, release_id)

    def document(self, name: ConfigName, version: str | None = None) -> ConfigDocumentView:
        if version is None:
            return self.active().document(name)
        rows = self.view.connection.rows(
            "SELECT raw FROM config_document WHERE name=? AND version=?", (name.value, version), limit=1
        )
        if not rows:
            raise ProblemError(ErrorCode.NOT_FOUND, "configuration document absent")
        raw = blob(rows[0][0])
        if hashlib.sha256(raw).hexdigest() != version:
            raise StorageUnavailable("corrupt")
        return ConfigDocumentView(name, version, raw)
