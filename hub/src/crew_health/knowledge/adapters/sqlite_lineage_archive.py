from __future__ import annotations

from hashlib import sha256

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import require_id
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import SqlValue, StorageSession
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.knowledge.adapters.release_codec import CanonicalReleaseCodec
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound
from crew_health.knowledge.domain.manifest import MemberSnapshot, ReleaseContent
from crew_health.knowledge.domain.passage import PassageArtifact
from crew_health.knowledge.domain.release import ReleaseMembership
from crew_health.knowledge.domain.results import HistoryRead


class SQLiteLineageArchive:
    def __init__(self, *, settings: HubSettings, codec: CanonicalReleaseCodec) -> None:
        self._settings, self._codec = settings, codec

    def append(self, uow: UnitOfWork, release_id: str, content: ReleaseContent) -> None:
        require_id(release_id)
        require_bound(len(content.memberships), self._settings.integer("knowledge.bundle.max_records"))
        for member in content.memberships:
            member.bind(release_id)
            self._artifact(uow.connection, member)
            raw = self._codec.encode_content(ReleaseContent((member,), (), ()))
            digest = sha256(
                canonical_json([release_id, member.passage.passage_id, raw.decode("utf-8")])
            ).hexdigest()
            result = uow.connection.execute(
                "INSERT OR IGNORE INTO knowledge_release_member VALUES (?,?,?,?)",
                (release_id, member.passage.passage_id, digest, raw),
            )
            if result.affected_rows == 0:
                old = uow.connection.rows(
                    "SELECT membership_hash,snapshot FROM knowledge_release_member WHERE release_id=? AND passage_id=?",
                    (release_id, member.passage.passage_id),
                    limit=1,
                )
                if old != ((digest, raw),):
                    raise EvidenceInvalid("membership_conflict")
            for status in member.uses:
                values: tuple[SqlValue, ...] = (
                    release_id,
                    member.passage.passage_id,
                    status.use,
                    status.status,
                    status.approval_ref,
                    status.successor_ref,
                )
                inserted = uow.connection.execute(
                    "INSERT OR IGNORE INTO knowledge_release_use VALUES (?,?,?,?,?,?)", values
                )
                if inserted.affected_rows == 0:
                    old = uow.connection.rows(
                        "SELECT release_id,passage_id,use,status,approval_ref,successor_ref FROM knowledge_release_use WHERE release_id=? AND passage_id=? AND use=?",
                        values[:3],
                        limit=1,
                    )
                    if old != (values,):
                        raise EvidenceInvalid("purpose_conflict")

    def _artifact(self, storage: StorageSession, member: MemberSnapshot) -> None:
        values = self._artifact_values(member.passage)
        result = storage.execute(
            "INSERT OR IGNORE INTO knowledge_passage_artifact VALUES (?,?,?,?,?,?,?)", values
        )
        if result.affected_rows == 0:
            rows = storage.rows(
                "SELECT passage_id,extraction_id,char_start,char_end,text_hash,immutable_body_hash,immutable_body FROM knowledge_passage_artifact WHERE passage_id=?",
                (member.passage.passage_id,),
                limit=1,
            )
            if rows != (values,):
                raise EvidenceInvalid("artifact_conflict")

    def _artifact_values(self, passage: PassageArtifact) -> tuple[SqlValue, ...]:
        raw = canonical_json(
            {
                "passage_id": passage.passage_id,
                "extraction_id": passage.extraction_id,
                "char_start": passage.char_start,
                "char_end": passage.char_end,
                "text_sha256": passage.text_sha256,
                "text": passage.text,
                "page_start": passage.page_start,
                "page_end": passage.page_end,
            }
        )
        return (
            passage.passage_id,
            passage.extraction_id,
            passage.char_start,
            passage.char_end,
            passage.text_sha256,
            sha256(raw).hexdigest(),
            raw,
        )

    def read(self, view: ReadSnapshot, release_id: str, passage_id: str) -> ReleaseMembership | None:
        rows = view.connection.rows(
            "SELECT membership_hash,snapshot FROM knowledge_release_member WHERE release_id=? AND passage_id=?",
            (release_id, passage_id),
            limit=1,
        )
        if not rows:
            return None
        digest, raw = rows[0]
        if not isinstance(digest, str) or not isinstance(raw, bytes):
            raise EvidenceInvalid("membership_storage")
        expected = sha256(canonical_json([release_id, passage_id, raw.decode("utf-8")])).hexdigest()
        if digest != expected:
            raise EvidenceInvalid("membership_hash")
        content = self._codec.decode_content(raw, release_id=release_id)
        if len(content.memberships) != 1 or content.memberships[0].passage.passage_id != passage_id:
            raise EvidenceInvalid("membership_target")
        member = content.memberships[0].bind(release_id)
        artifact = view.connection.rows(
            "SELECT passage_id,extraction_id,char_start,char_end,text_hash,immutable_body_hash,immutable_body FROM knowledge_passage_artifact WHERE passage_id=?",
            (passage_id,),
            limit=1,
        )
        if artifact != (self._artifact_values(member.passage),):
            raise EvidenceInvalid("artifact_hash")
        uses = view.connection.rows(
            "SELECT use,status,approval_ref,successor_ref FROM knowledge_release_use WHERE release_id=? AND passage_id=? ORDER BY use",
            (release_id, passage_id),
            limit=4,
        )
        expected_uses = tuple(
            (status.use, status.status, status.approval_ref, status.successor_ref)
            for status in sorted(member.uses, key=lambda status: status.use)
        )
        if uses != expected_uses:
            raise EvidenceInvalid("purpose_storage")
        return member

    def history(self, view: ReadSnapshot, passage_id: str, cursor: str | None, limit: int) -> HistoryRead:
        maximum = min(
            self._settings.integer("knowledge.query.limit_max"),
            self._settings.integer("storage.max_rows") - 1,
        )
        require_bound(limit, maximum)
        if limit < 1:
            raise EvidenceInvalid("page_limit")
        if cursor is not None:
            require_id(cursor)
        rows = view.connection.rows(
            "SELECT release_id FROM knowledge_release_member WHERE passage_id=? AND (? IS NULL OR release_id>?) ORDER BY release_id LIMIT ?",
            (passage_id, cursor, cursor, limit + 1),
            limit=limit + 1,
        )
        items = []
        for row in rows[:limit]:
            if not isinstance(row[0], str):
                raise EvidenceInvalid("history_storage")
            member = self.read(view, row[0], passage_id)
            if member is None:
                raise EvidenceInvalid("history_storage")
            items.append(member)
        return HistoryRead(tuple(items), items[-1].release_id if len(rows) > limit else None)
