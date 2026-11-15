from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from crew_health.model.domain.policy import check_gate
from crew_health.model.domain.release import ModelRefused, object_value


class PromotionEvidence(Protocol):
    def reports(self, release_id: str) -> tuple[Mapping[str, object], ...]: ...
    def lineage(self, release_id: str) -> bool: ...


def promote(
    document: Mapping[str, object],
    gate: Mapping[str, object],
    policy: Mapping[str, object],
    evidence: PromotionEvidence,
) -> dict[str, object]:
    if document.get("mode") != "live" or document.get("status") != "candidate":
        raise ModelRefused("not_candidate")
    release_id = str(document["release_id"])
    check_gate(release_id, gate, policy)
    if not evidence.lineage(release_id):
        raise ModelRefused("lineage_broken")
    reports = evidence.reports(release_id)
    weights = object_value(document["weights"])
    runtime = object_value(document["runtime"])
    measured = {
        str(r.get("probe"))
        for r in reports
        if r.get("verdict") == "pass"
        and r.get("measured") is True
        and r.get("weights_sha256") == weights["weights_sha256"]
        and r.get("runtime_commit") == runtime["commit"]
    }
    if measured != {f"P{n}" for n in range(1, 9)}:
        raise ModelRefused("probes_missing")
    result = dict(document)
    result["status"] = "promoted"
    return result
