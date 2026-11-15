"""T-E18-07/17/28/31: complete cases and source-checked engineering labels."""

from copy import deepcopy
from pathlib import Path

import pytest
from crew_health.kernel.testing import MemoryIds

from crew_health_bench.adapters.case_documents import CaseDocuments
from crew_health_bench.domain.errors import BenchError

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "R23", "T-E18-28", "T-E18-31")]
ROOT = Path(__file__).parents[1]


def test_authored_case_round_trip_validates_identity_and_never_includes_hidden_text() -> None:
    documents = CaseDocuments(ROOT / "schemas", max_file_bytes=1048576, ids=MemoryIds(identifiers=()))
    fixture = ROOT / "fixtures/case-document.json"
    case = documents.load(fixture)
    assert case.case.suite.value == "nasa_scenario"
    assert case.case.revealable is not None
    assert case.visible.opening_complaint
    assert "rubric" not in case.visible.model_dump_json()
    assert documents.parse(case.document.model_dump_json().encode("utf-8")).case == case.case


def test_storm_shelter_label_is_checked_independently_of_warning_configuration() -> None:
    documents = CaseDocuments(ROOT / "schemas", max_file_bytes=1048576, ids=MemoryIds(identifiers=()))
    hidden = documents.load_hidden(ROOT / "fixtures/hidden-part.json")
    assert hidden.expected_urgency == "emergency"
    data = deepcopy(hidden.model_dump(mode="json"))
    data["expected_urgency"] = "act"
    with pytest.raises(BenchError, match="case_invalid"):
        documents.parse_hidden(data)
    data = deepcopy(hidden.model_dump(mode="json"))
    data["source"]["text"] = "config:warning-rules/definitions/level"
    with pytest.raises(BenchError, match="case_invalid"):
        documents.parse_hidden(data)


def test_case_identity_extra_fields_and_unmeasured_values_fail_closed() -> None:
    documents = CaseDocuments(ROOT / "schemas", max_file_bytes=1048576, ids=MemoryIds(identifiers=()))
    case = documents.load(ROOT / "fixtures/case-document.json")
    for changes in ({"case_id": "wrong"}, {"rubric": "not visible"}, {"canary": ""}):
        with pytest.raises(BenchError):
            documents.parse_mapping({**case.document.model_dump(mode="json"), **changes})
    value = case.document.model_dump(mode="json")
    value["revealable"]["capabilities"][0]["answer"]["outcome"] = "unavailable"
    with pytest.raises(BenchError):
        documents.parse_mapping(value)
