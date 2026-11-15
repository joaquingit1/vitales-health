from __future__ import annotations

from hashlib import sha256

from pydantic import ValidationError

from crew_health.kernel.settings import HubSettings
from crew_health.knowledge.adapters._policy_models import ReleasePolicyInput
from crew_health.knowledge.adapters.registry_file import byte_limit, parse_json
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound
from crew_health.knowledge.domain.policy import PurposeRule, ReleasePolicy
from crew_health.knowledge.domain.segmentation import SegmentationPolicy


def load_policy(raw: bytes, *, settings: HubSettings) -> ReleasePolicy:
    value = parse_json(
        raw,
        max_bytes=byte_limit(settings, "knowledge.bundle.max_manifest_bytes"),
        max_depth=settings.integer("knowledge.bundle.max_json_depth"),
    )
    try:
        parsed = ReleasePolicyInput.model_validate(value, strict=True)
    except ValidationError as error:
        raise EvidenceInvalid("release_policy") from error
    maximum = settings.integer("knowledge.bundle.max_records")
    if len({source.root for source in parsed.provenance.source_ids}) != len(parsed.provenance.source_ids):
        raise EvidenceInvalid("policy_provenance")
    require_bound(len(parsed.source_hierarchy), maximum)
    require_bound(len(parsed.tracking_parameters), maximum)
    for rule in parsed.purposes:
        require_bound(len(rule.source_kinds), maximum)
    segmentation = SegmentationPolicy(
        parsed.segmentation.min_words,
        parsed.segmentation.max_words,
        parsed.segmentation.overlap_words,
    )
    require_bound(segmentation.max_words, settings.integer("knowledge.passage.max_chars"))
    return ReleasePolicy(
        tuple(
            PurposeRule(
                rule.use,
                tuple(kind.root for kind in rule.source_kinds),
                tuple(rule.completeness),
                rule.partial_acknowledgment_required,
            )
            for rule in parsed.purposes
        ),
        segmentation,
        tuple(parameter.root for parameter in parsed.tracking_parameters),
        tuple(kind.root for kind in parsed.source_hierarchy),
        sha256(raw).hexdigest(),
    )
