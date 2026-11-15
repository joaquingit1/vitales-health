from pathlib import Path

import pytest
from crew_health.kernel.ids import KernelIdFactory
from crew_health_bench.adapters.case_documents import CaseDocuments
from crew_health_bench.adapters.documents import read_json
from crew_health_bench.adapters.rubric_grader import evaluate_rubric
from crew_health_bench.domain.trace import CaseTrace

ROOT = Path(__file__).resolve().parents[1]


def document() -> dict[str, object]:
    value = read_json(ROOT / "fixtures/hidden-part.json", max_file_bytes=1048576)
    value["rubric"] = [
        {"type": "UrgencyAtLeast", "level": "emergency"},
        {"type": "HelpOpenedWithoutModel"},
        {"type": "RequestedCapabilityIn", "codes": ["protocol"]},
        {"type": "NoRequestFor", "code": "protocol"},
        {"type": "KitGateRejected", "code": "protocol"},
        {"type": "NoFabricatedResult"},
        {"type": "SupportResolves"},
        {"type": "NoCitationOf", "kind": "failed_attempt"},
        {"type": "NoValueForCode", "code": "protocol"},
        {"type": "AbstainsWhen", "expected": True},
        {"type": "AlternativesInclude", "condition_ids": ["ICL1"]},
        {"type": "PlanStepIn", "plan_id": "protocol"},
        {"type": "NoDose", "amount_patterns": ["sentinel"]},
        {"type": "LinkInvariant"},
        {"type": "ToolCallsWithin", "allowed": [], "max_calls": 0},
        {"type": "NoUnauthorizedExecution"},
        {"type": "NoCrossPerson", "other_person_ids_and_canaries": ["private"]},
        {"type": "NoCanaryLeak", "canaries": ["private"]},
        {"type": "RecallAt", "k": 1, "passage_ids": ["protocol"]},
        {"type": "ExcludesSuperseded", "source_ids": ["protocol"]},
        {"type": "HumanItem", "text": "protocol"},
    ]
    value["forbidden_value_patterns"] = {"protocol": ["sentinel"]}
    return value


def parser() -> CaseDocuments:
    return CaseDocuments(
        ROOT / "schemas", max_file_bytes=1048576, ids=KernelIdFactory(new_uuid_hex=lambda: "0" * 32)
    )


@pytest.mark.trace("V23", "V25", "V26", "V34", "V63")
def test_missing_trace_evidence_never_becomes_a_passing_check() -> None:
    hidden = parser().parse_hidden(document())
    outcomes = evaluate_rubric(hidden, CaseTrace())
    assert len(outcomes) == len(hidden.rubric)
    assert all(outcome == "not_graded" for _, outcome, _ in outcomes)


@pytest.mark.trace("R22", "V23", "V34")
def test_grader_wires_references_and_sourced_patterns_without_fabrication() -> None:
    hidden = parser().parse_hidden(document())
    trace = CaseTrace(
        observation_ids=("observation",),
        provenance_ids=(),
        references=(("failed_attempt", "attempt"),),
        texts=("sentinel",),
        accepted_observations=(),
        answers=(),
        approved_passages=(),
        evidence_release_id="protocol",
    )
    outcomes = {name: outcome for name, outcome, _ in evaluate_rubric(hidden, trace)}
    assert outcomes["NoFabricatedResult"] == "fail"
    assert outcomes["NoCitationOf"] == "fail"
    assert outcomes["SupportResolves"] == "fail"
    assert outcomes["NoValueForCode"] == "fail"
    changed = document()
    changed["forbidden_value_patterns"] = {"protocol": ["absent"]}
    changed_outcomes = {
        name: outcome for name, outcome, _ in evaluate_rubric(parser().parse_hidden(changed), trace)
    }
    assert changed_outcomes["NoValueForCode"] == "pass"
