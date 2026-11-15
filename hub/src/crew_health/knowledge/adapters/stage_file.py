from __future__ import annotations

from pydantic import ValidationError

from crew_health.kernel.settings import HubSettings
from crew_health.knowledge.adapters._stage_models import StageMapInput
from crew_health.knowledge.adapters.registry_file import byte_limit, parse_json
from crew_health.knowledge.domain.catalog import CaptureFacts, Completeness, assess_ceiling
from crew_health.knowledge.domain.errors import EvidenceInvalid


class StageMap:
    def __init__(self, parsed: StageMapInput) -> None:
        self._methods: dict[str, Completeness] = {row.method: row.ceiling for row in parsed.methods}
        if len(self._methods) != len(parsed.methods):
            raise EvidenceInvalid("duplicate_capture_method")

    def ceiling(self, facts: CaptureFacts) -> Completeness:
        configured = self._methods.get(facts.method)
        if configured is None:
            raise EvidenceInvalid("capture_method_unavailable")
        measured = assess_ceiling(facts)
        order: tuple[Completeness, ...] = ("inaccessible", "summary_only", "partial", "full")
        return order[min(order.index(configured), order.index(measured))]


def load_stage_map(raw: bytes, *, settings: HubSettings) -> StageMap:
    parsed = parse_json(
        raw,
        max_bytes=byte_limit(settings, "knowledge.bundle.max_manifest_bytes"),
        max_depth=settings.integer("knowledge.bundle.max_json_depth"),
    )
    try:
        return StageMap(StageMapInput.model_validate(parsed, strict=True))
    except ValidationError as error:
        raise EvidenceInvalid("stage_map") from error
