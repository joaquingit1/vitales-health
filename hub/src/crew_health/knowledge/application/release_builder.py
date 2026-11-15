from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256

from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.knowledge.application.card_builder import build_cards
from crew_health.knowledge.application.claim_builder import validate_claims
from crew_health.knowledge.application.ports import (
    ArtifactReader,
    EvidenceQueryPort,
    BundleWriter,
    ReleaseCodec,
    ReleaseHistoryReader,
    ReviewAuthority,
)
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound
from crew_health.knowledge.domain.export import (
    ArtifactReference,
    ReleaseTarget,
    ReviewExport,
    UseApproval,
    review_checkpoint,
)
from crew_health.knowledge.domain.manifest import (
    ManifestMember,
    MemberSnapshot,
    ReleaseContent,
    ReleaseManifest,
)
from crew_health.knowledge.domain.passage import PassageArtifact
from crew_health.knowledge.domain.policy import ReleasePolicy, source_eligible
from crew_health.knowledge.domain.release import UseStatus
from crew_health.knowledge.domain.review import (
    PathwayCardTarget,
    ReviewTarget,
    clinical_review_label,
    effective_reviews,
)


@dataclass(frozen=True, slots=True)
class Built:
    manifest: ReleaseManifest
    content: ReleaseContent
    artifact: ArtifactReference
    bundle: bytes


@dataclass(frozen=True, slots=True)
class BuildRejected:
    findings: tuple[str, ...]


class ReleaseBuilder:
    def __init__(
        self,
        *,
        ids: IdFactory,
        artifacts: ArtifactReader,
        settings: HubSettings,
        policy: ReleasePolicy,
        codec: ReleaseCodec,
        writer: BundleWriter,
        authority: ReviewAuthority,
        history: ReleaseHistoryReader | None = None,
        previous: EvidenceQueryPort | None = None,
    ) -> None:
        self._ids, self._artifacts, self._settings, self._policy = ids, artifacts, settings, policy
        self._codec, self._writer, self._authority, self._history = codec, writer, authority, history
        self._previous = previous

    def build(self, review_export: ReviewExport) -> Built | BuildRejected:
        try:
            return self._build(review_export)
        except EvidenceInvalid as error:
            return BuildRejected((str(error),))

    def _build(self, export: ReviewExport) -> Built:
        self._bounds(export)
        if export.policy_sha256 != self._policy.sha256:
            raise EvidenceInvalid("policy_pin")
        raw_export = self._codec.encode_export(export)
        export_hash = sha256(raw_export).hexdigest()
        self._authority.verify(export_hash, review_checkpoint(export))
        heads = self._reviews(export)
        members = self._passages(export, heads)
        content = ReleaseContent(members, build_cards(export, heads, members, self._settings),
            validate_claims(export, members, self._previous, self._settings))
        files = self._files(export)
        files["review-export.json"] = raw_export
        files["content.json"] = self._codec.encode_content(content)
        if export.previous_release_id is not None:
            if self._history is None:
                raise EvidenceInvalid("history_unavailable")
            previous = self._history.members(export.previous_release_id)
            require_bound(len(previous), self._settings.integer("knowledge.bundle.max_members"))
            for name, raw in previous:
                self._add(files, "history/" + export.previous_release_id + "/" + name, raw)
        manifest = ReleaseManifest(
            "evr_pending",
            export.format_version,
            export.registry_sha256,
            export.policy_sha256,
            export.effective_contract_sha256,
            export.decision_overlay_sha256,
            export_hash,
            export.previous_release_id,
            export.build_at,
            export.author,
            export.embedding_pin,
            tuple(
                ManifestMember(name, sha256(raw).hexdigest(), len(raw)) for name, raw in sorted(files.items())
            ),
        )
        digest = sha256(self._codec.encode_manifest(manifest, body_only=True)).hexdigest()
        manifest = replace(manifest, release_id=self._ids.derived("evr", digest))
        files["manifest.json"] = self._codec.encode_manifest(manifest)
        bundle = self._writer.write(tuple(sorted(files.items())))
        return Built(manifest, content, ArtifactReference(sha256(bundle).hexdigest(), len(bundle)), bundle)

    def _bounds(self, export: ReviewExport) -> None:
        for rows in (
            export.targets,
            export.source_reviews,
            export.approvals,
            export.claims,
            export.claim_reviews,
        ):
            require_bound(len(rows), self._settings.integer("knowledge.review.max_records"))
        if export.embedding_pin is not None:
            require_bound(export.embedding_pin.dims, self._settings.integer("knowledge.vectors.max_dims"))
        for target in export.targets:
            require_bound(target.original.byte_count, self._bytes("knowledge.capture.max_bytes"))
            require_bound(len(target.source_ids), self._settings.integer("knowledge.query.max_source_ids"))
            require_bound(len(target.extraction.pages), self._settings.integer("knowledge.forms.max_pages"))

    def _bytes(self, key: str) -> int:
        value = self._settings.quantity(key)
        if value.unit.code != "By" or value.value != value.value.to_integral_value():
            raise EvidenceInvalid("byte_setting")
        return int(value.value)

    def _reviews(self, export: ReviewExport) -> frozenset[str]:
        known: dict[ReviewTarget | PathwayCardTarget, str] = {target.review_target(): target.extraction.extracted_by for target in export.targets}
        known.update({PathwayCardTarget(card.condition_id, card.content_sha256): card.authored_by for card in export.cards})
        records = {review.review_id: review for review in export.source_reviews}
        if len(records) != len(export.source_reviews):
            raise EvidenceInvalid("duplicate_review")
        receipts = frozenset(export.audit_receipts)
        superseded = set()
        for review in export.source_reviews:
            if review.target not in known or review.audit_receipt not in receipts:
                raise EvidenceInvalid("review_target_or_receipt")
            if review.supersedes_review_id is not None:
                superseded.add(review.supersedes_review_id)
        heads = frozenset(records.keys() - superseded)
        if heads != frozenset(export.review_heads):
            raise EvidenceInvalid("review_heads")
        for target, author in known.items():
            for kind in ("technical_check", "clinical_review"):
                effective_reviews(
                    tuple(export.source_reviews),
                    target,
                    kind,
                    author,
                    max_records=self._settings.integer("knowledge.review.max_records"),
                    max_chain_depth=self._settings.integer("knowledge.review.max_chain_depth"),
                )
        return heads

    def _passages(self, export: ReviewExport, heads: frozenset[str]) -> tuple[MemberSnapshot, ...]:
        targets = {target.review_target(): target for target in export.targets}
        result: dict[str, MemberSnapshot] = {}
        for approval in export.approvals:
            if isinstance(approval.target, PathwayCardTarget):
                continue
            target = targets.get(approval.target) if isinstance(approval.target, ReviewTarget) else None
            if target is None:
                raise EvidenceInvalid("approval_target")
            self._approval(export, approval, target, heads)
            label = clinical_review_label(
                export.source_reviews,
                target.review_target(),
                target.extraction.extracted_by,
                max_records=self._settings.integer("knowledge.review.max_records"),
                max_chain_depth=self._settings.integer("knowledge.review.max_chain_depth"),
            )
            event = approval.events[-1]
            status = UseStatus(
                approval.use,
                "approved" if event.state == "active" else event.state,
                approval.approval_id,
                event.successor_ref,
            )
            require_bound(len(approval.spans), self._settings.integer("knowledge.bundle.max_records"))
            for span in approval.spans:
                passage = PassageArtifact.create(
                    ids=self._ids,
                    extraction=target.extraction,
                    char_start=span.char_start,
                    char_end=span.char_end,
                    max_chars=self._settings.integer("knowledge.passage.max_chars"),
                )
                prior = result.get(passage.passage_id)
                uses = (status,) if prior is None else (*prior.uses, status)
                member = MemberSnapshot(
                    passage,
                    target.extraction,
                    target.source_ids,
                    target.license,
                    uses,
                    label,
                    target.environments,
                    span.section,
                    target.document.withdrawn,
                    False,
                )
                member.bind("evr_pending")
                result[passage.passage_id] = member
                require_bound(len(result), self._settings.integer("knowledge.bundle.max_records"))
        return tuple(result[key] for key in sorted(result))

    def _approval(
        self, export: ReviewExport, approval: UseApproval, target: ReleaseTarget, heads: frozenset[str]
    ) -> None:
        assessment = target.document.assessment
        if assessment is None or approval.assessment_id != assessment.assessment_id:
            raise EvidenceInvalid("assessment_target")
        if approval.policy_sha256 != export.policy_sha256 or approval.license_id != target.license.license_id:
            raise EvidenceInvalid("approval_pin")
        if approval.approved_by == target.extraction.extracted_by:
            raise EvidenceInvalid("reviewer_is_author")
        if any(event.audit_receipt not in export.audit_receipts for event in approval.events):
            raise EvidenceInvalid("approval_receipt")
        by_id = {review.review_id: review for review in export.source_reviews}
        technical = by_id.get(approval.technical_check_id) if approval.technical_check_id is not None else None
        if (
            technical is None
            or technical.review_id not in heads
            or technical.target != approval.target
            or technical.kind != "technical_check"
        ):
            raise EvidenceInvalid("technical_check")
        if technical.source_kind != target.source_kind or technical.completeness_confirmed not in (
            "full",
            "partial",
        ):
            raise EvidenceInvalid("technical_facts")
        if technical.completeness_confirmed == "full" and assessment.completeness != "full":
            raise EvidenceInvalid("completeness")
        verdict = effective_reviews(
            export.source_reviews,
            target.review_target(),
            "technical_check",
            target.extraction.extracted_by,
            max_records=self._settings.integer("knowledge.review.max_records"),
            max_chain_depth=self._settings.integer("knowledge.review.max_chain_depth"),
        )
        rule = next((rule for rule in self._policy.purposes if rule.use == approval.use), None)
        if rule is None or not source_eligible(
            rule,
            use=approval.use,
            source_kind=target.source_kind,
            completeness=technical.completeness_confirmed,
            partial_acknowledged=approval.partial_acknowledged,
            technical_verdict=verdict,
            permits_text=target.license.permits_text,
        ):
            raise EvidenceInvalid("purpose_not_allowed")
        if approval.clinical_review_id is not None:
            clinical = by_id.get(approval.clinical_review_id)
            if (
                clinical is None
                or clinical.review_id not in heads
                or clinical.kind != "clinical_review"
                or clinical.target != approval.target
                or clinical.verdict != "pass"
            ):
                raise EvidenceInvalid("clinical_review_pin")

    def _files(self, export: ReviewExport) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        total = 0
        for target in export.targets:
            total += target.original.byte_count
            require_bound(total * 3, self._bytes("knowledge.install.rss_bytes"))
            raw = self._artifacts.read(target.original)
            if len(raw) != target.original.byte_count or sha256(raw).hexdigest() != target.original.sha256:
                raise EvidenceInvalid("original_hash")
            self._add(files, "originals/" + target.original.sha256, raw)
            self._add(files, "texts/" + target.extraction.text_sha256, target.extraction.text.encode("utf-8"))
        return files

    def _add(self, files: dict[str, bytes], name: str, raw: bytes) -> None:
        ManifestMember(name, sha256(raw).hexdigest(), len(raw))
        if name in files and files[name] != raw:
            raise EvidenceInvalid("artifact_conflict")
        files[name] = raw
        require_bound(len(files), self._settings.integer("knowledge.bundle.max_members"))
