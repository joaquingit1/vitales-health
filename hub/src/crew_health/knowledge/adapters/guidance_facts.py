from __future__ import annotations

from crew_health.guidance.application.ports import ContentFactSet, ContentRef, Known, Unknown
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import ReadSnapshot
from crew_health.knowledge.application.ports import EvidenceQueryPort, SourceRegistryPort
from crew_health.knowledge.domain.registry import RegistryUnavailable
from crew_health.knowledge.domain.results import Eligible


def _unknown(reason: str) -> ContentFactSet:
    missing = Unknown("content", reason)
    return ContentFactSet(
        missing, missing, "not_clinically_reviewed", missing, missing, False, missing, None, None
    )


class EvidenceContentFacts:
    def __init__(
        self,
        snapshot: ReadSnapshot,
        *,
        evidence_release_id: str,
        reader: EvidenceQueryPort,
        sources: SourceRegistryPort,
    ) -> None:
        self._snapshot = snapshot
        self._config_release_id = snapshot.release.release_id
        self._evidence_release_id = require_id(evidence_release_id)
        self._reader, self._sources = reader, sources

    def content(
        self,
        snapshot: ReadSnapshot,
        content: ContentRef,
        *,
        purpose: str,
        release_id: str,
    ) -> ContentFactSet:
        if snapshot is not self._snapshot or release_id != self._config_release_id:
            return _unknown("snapshot_pin")
        if content.kind != "passage":
            return _unknown("content_kind")
        read = self._reader.read_approved(self._evidence_release_id, content.id, purpose)
        if not isinstance(read, Eligible):
            return _unknown("purpose_or_release")
        member = read.membership
        if (
            member.release_id != self._evidence_release_id
            or member.passage.text_sha256 != content.content_sha256
        ):
            return _unknown("content_hash")
        resolution = self._sources.resolve_many(member.source_ids)
        if isinstance(resolution, RegistryUnavailable):
            return _unknown("registry_unavailable")
        if resolution.unknown or resolution.conflicts:
            return _unknown("source_unresolved")
        return ContentFactSet(
            Known(True),
            Known(True),
            member.clinical_review,
            Known(member.withdrawn),
            Known(member.simulation_only),
            True,
            Unknown("content", "applicability_unknown")
            if member.environments is None
            else Known(member.environments),
            None,
            None,
        )
