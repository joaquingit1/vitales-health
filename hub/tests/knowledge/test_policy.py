import json
from pathlib import Path

import pytest

from crew_health.kernel.testing import MemorySettings
from crew_health.knowledge.adapters.policy_file import load_policy
from crew_health.knowledge.domain.errors import EvidenceInvalid
from crew_health.knowledge.domain.policy import source_eligible


@pytest.mark.trace("T-E10-09", "T-E10-21", "T-E10-24", "I3", "I4")
def test_policy_preserves_independent_golden_purpose_cases(settings: MemorySettings) -> None:
    policy = load_policy(Path("knowledge/policy/release-policy.json").read_bytes(), settings=settings)
    instruction = next(rule for rule in policy.purposes if rule.use == "patient_instruction")
    for kind, completeness, expected in (
        ("clinical_guideline", "full", True),
        ("device_manual", "full", True),
        ("modeling_cliff", "full", False),
        ("standard", "full", False),
        ("technical_brief", "full", False),
        ("clinical_guideline", "partial", False),
        ("clinical_guideline", "summary_only", False),
    ):
        assert (
            source_eligible(
                instruction,
                use="patient_instruction",
                source_kind=kind,
                completeness=completeness,
                partial_acknowledged=False,
                technical_verdict="pass",
                permits_text=True,
            )
            is expected
        )


@pytest.mark.trace("T-E10-31", "C11")
def test_versioned_policy_edit_changes_segmentation_without_code(settings: MemorySettings) -> None:
    path = Path("knowledge/policy/release-policy.json")
    original = load_policy(path.read_bytes(), settings=settings)
    candidate = json.loads(path.read_bytes())
    candidate["segmentation"]["overlap_words"] = 0
    changed = load_policy(json.dumps(candidate).encode("utf-8"), settings=settings)
    assert changed.segmentation.overlap_words == 0
    assert original.segmentation.overlap_words != changed.segmentation.overlap_words
    assert original.purposes == changed.purposes


@pytest.mark.trace("T-E10-24", "T-E10-55", "I3", "I8")
def test_invalid_policy_never_weakens_instruction_checks(settings: MemorySettings) -> None:
    for key, value in (("completeness", ["partial"]), ("source_kinds", ["modeling_cliff"])):
        candidate = json.loads(Path("knowledge/policy/release-policy.json").read_bytes())
        rule = next(rule for rule in candidate["purposes"] if rule["use"] == "patient_instruction")
        rule[key] = value
        with pytest.raises(EvidenceInvalid):
            load_policy(json.dumps(candidate).encode("utf-8"), settings=settings)


@pytest.mark.trace("T-E10-24", "T-E10-55", "I3", "I8")
def test_policy_unique_item_constraints_are_not_lost_in_generated_models(settings: MemorySettings) -> None:
    for field in ("source_kinds", "completeness"):
        value = json.loads(Path("knowledge/policy/release-policy.json").read_bytes())
        value["purposes"][0][field].append(value["purposes"][0][field][0])
        with pytest.raises(EvidenceInvalid):
            load_policy(json.dumps(value).encode("utf-8"), settings=settings)
    for field in ("source_hierarchy", "tracking_parameters"):
        value = json.loads(Path("knowledge/policy/release-policy.json").read_bytes())
        value[field] = ["duplicate", "duplicate"]
        with pytest.raises(EvidenceInvalid):
            load_policy(json.dumps(value).encode("utf-8"), settings=settings)
    value = json.loads(Path("knowledge/policy/release-policy.json").read_bytes())
    value["provenance"]["source_ids"] *= 2
    with pytest.raises(EvidenceInvalid):
        load_policy(json.dumps(value).encode("utf-8"), settings=settings)
