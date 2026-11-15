from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.settings import HubSettings
from crew_health.knowledge.adapters._evidence_content_models import ReleaseContent as ReleaseContentInput
from crew_health.knowledge.adapters._export_models import ReviewExport as ReviewExportInput
from crew_health.knowledge.adapters._release_manifest_models import ReleaseManifest as ReleaseManifestInput
from crew_health.knowledge.adapters.registry_file import byte_limit, parse_json
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound
from crew_health.knowledge.domain.export import ReviewExport
from crew_health.knowledge.domain.manifest import ReleaseContent, ReleaseManifest


class CanonicalReleaseCodec:
    def __init__(self, *, settings: HubSettings) -> None:
        self._settings = settings

    def _encode(self, value: object) -> bytes:
        pending = [value]
        for _ in range(self._settings.integer("knowledge.bundle.max_records")):
            if not pending:
                break
            item = pending.pop()
            if isinstance(item, dict):
                environments = item.get("environments")
                if isinstance(environments, list) and all(isinstance(e, str) for e in environments):
                    environments.sort()
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        else:
            if pending:
                raise EvidenceInvalid("json_records")
        try:
            return canonical_json(value)
        except (ValueError, UnicodeError) as error:
            raise EvidenceInvalid("canonical_json") from error

    def encode_export(self, export: ReviewExport) -> bytes:
        return self._encode(TypeAdapter(ReviewExport).dump_python(export, mode="json"))

    def encode_content(self, content: ReleaseContent) -> bytes:
        return self._encode(TypeAdapter(ReleaseContent).dump_python(content, mode="json"))

    def encode_manifest(self, manifest: ReleaseManifest, *, body_only: bool = False) -> bytes:
        value: object = TypeAdapter(ReleaseManifest).dump_python(manifest, mode="json")
        if not isinstance(value, dict):
            raise EvidenceInvalid("manifest")
        if body_only:
            del value["release_id"]
        return self._encode(value)

    def decode_export(self, raw: bytes) -> ReviewExport:
        self._parse(raw)
        try:
            ReviewExportInput.model_validate_json(raw, strict=True)
            value = TypeAdapter(ReviewExport).validate_json(raw, strict=True)
        except ValidationError as error:
            raise EvidenceInvalid("review_export") from error
        require_bound(len(value.source_reviews), self._settings.integer("knowledge.review.max_records"))
        require_bound(len(value.approvals), self._settings.integer("knowledge.review.max_records"))
        return value

    def decode_content(self, raw: bytes, *, release_id: str) -> ReleaseContent:
        self._parse(raw)
        try:
            ReleaseContentInput.model_validate_json(raw, strict=True)
            value = TypeAdapter(ReleaseContent).validate_json(raw, strict=True)
        except ValidationError as error:
            raise EvidenceInvalid("evidence_content") from error
        require_bound(len(value.memberships), self._settings.integer("knowledge.bundle.max_records"))
        for member in value.memberships:
            member.bind(release_id)
        return value

    def decode_manifest(self, raw: bytes) -> ReleaseManifest:
        require_bound(len(raw), byte_limit(self._settings, "knowledge.bundle.max_manifest_bytes"))
        self._parse(raw)
        try:
            ReleaseManifestInput.model_validate_json(raw, strict=True)
            value = TypeAdapter(ReleaseManifest).validate_json(raw, strict=True)
        except ValidationError as error:
            raise EvidenceInvalid("release_manifest") from error
        require_bound(len(value.members), self._settings.integer("knowledge.bundle.max_members"))
        return value

    def _parse(self, raw: bytes) -> None:
        parse_json(
            raw,
            max_bytes=byte_limit(self._settings, "knowledge.bundle.max_member_bytes"),
            max_depth=self._settings.integer("knowledge.bundle.max_json_depth"),
        )
