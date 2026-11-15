"""T-E18-07/17/28/31: complete cases and source-checked engineering labels."""

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
from crew_health.kernel.testing import MemoryIds

from crew_health_bench.adapters.case_documents import CaseDocuments
from crew_health_bench.adapters.documents import load_document, read_json
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.labels import LabelConstraint, label_constraints, require_independent_labels

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "R23", "T-E18-28", "T-E18-31")]
ROOT = Path(__file__).parents[1]


def test_authored_case_round_trip_validates_identity_and_never_includes_hidden_text() -> None:
    documents = CaseDocuments.from_config(
        ROOT / "config", max_file_bytes=1048576, ids=MemoryIds(identifiers=())
    )
    fixture = ROOT / "fixtures/case-document.json"
    case = documents.load(fixture)
    assert case.case.suite.value == "nasa_scenario"
    assert case.case.revealable is not None
    assert case.visible.opening_complaint
    assert "rubric" not in case.visible.model_dump_json()
    assert documents.parse(case.document.model_dump_json().encode("utf-8")).case == case.case


def test_storm_shelter_label_is_checked_independently_of_warning_configuration() -> None:
    documents = CaseDocuments.from_config(
        ROOT / "config", max_file_bytes=1048576, ids=MemoryIds(identifiers=())
    )
    hidden = documents.load_hidden(ROOT / "fixtures/hidden-part.json", family_id="radiation")
    assert hidden.expected_urgency == "emergency"
    data = deepcopy(hidden.model_dump(mode="json"))
    data["expected_urgency"] = "act"
    with pytest.raises(BenchError, match="case_invalid"):
        documents.parse_hidden(data, family_id="radiation")
    # The D2 row names the radiation family: a label of another family citing the same decision is not bound
    # by it.
    assert documents.parse_hidden(data, family_id="thermal").expected_urgency == "act"
    data = deepcopy(hidden.model_dump(mode="json"))
    data["source"]["text"] = "config:warning-rules/definitions/level"
    with pytest.raises(BenchError, match="case_invalid"):
        documents.parse_hidden(data, family_id="radiation")


def _config_with_constraints(tmp_path: Path, rows: object) -> Path:
    config = tmp_path / "config"
    shutil.copytree(ROOT / "config", config)
    suites = json.loads((config / "suites.json").read_bytes())
    suites["label_constraints"] = rows
    (config / "suites.json").write_text(json.dumps(suites), encoding="utf-8")
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    return config


def test_label_constraints_come_from_the_suites_configuration_row(tmp_path: Path) -> None:
    """T-E18-17, T-E18-31: the D2 rule is the `suites.json` row; editing the row changes the check, no
    code."""
    suites = load_document(ROOT / "config/suites.json", max_file_bytes=65536)
    assert label_constraints(suites) == (
        LabelConstraint("radiation", "COORDINATOR-DECISIONS D2", "emergency"),
    )
    hidden = read_json(ROOT / "fixtures/hidden-part.json", max_file_bytes=65536)
    hidden["expected_urgency"] = "act"
    without = CaseDocuments.from_config(
        _config_with_constraints(tmp_path / "none", []), max_file_bytes=1048576, ids=MemoryIds(identifiers=())
    )
    assert without.parse_hidden(hidden, family_id="radiation").expected_urgency == "act"
    acting = CaseDocuments.from_config(
        _config_with_constraints(
            tmp_path / "act",
            [{"family_id": "radiation", "source": "COORDINATOR-DECISIONS D2", "expected_urgency": "act"}],
        ),
        max_file_bytes=1048576,
        ids=MemoryIds(identifiers=()),
    )
    assert acting.parse_hidden(hidden, family_id="radiation").expected_urgency == "act"
    hidden["expected_urgency"] = "emergency"
    with pytest.raises(BenchError, match="case_invalid"):
        acting.parse_hidden(hidden, family_id="radiation")


@pytest.mark.parametrize(
    "rows",
    [
        [{"family_id": "radiation", "source": "COORDINATOR-DECISIONS D2", "expected_urgency": "severe"}],
        [{"family_id": "radiation", "source": "COORDINATOR-DECISIONS D2"}],
        [{"family_id": "radiation", "source": "", "expected_urgency": "emergency"}],
        [
            {"family_id": "radiation", "source": "COORDINATOR-DECISIONS D2", "expected_urgency": "emergency"},
            {"family_id": "radiation", "source": "COORDINATOR-DECISIONS D2", "expected_urgency": "act"},
        ],
        [{"family_id": " ", "source": "COORDINATOR-DECISIONS D2", "expected_urgency": "emergency"}],
        {"family_id": "radiation"},
    ],
)
def test_a_malformed_label_constraint_row_refuses_the_configuration(tmp_path: Path, rows: object) -> None:
    with pytest.raises(BenchError, match="case_invalid"):
        CaseDocuments.from_config(
            _config_with_constraints(tmp_path, rows), max_file_bytes=1048576, ids=MemoryIds(identifiers=())
        )


def test_case_identity_extra_fields_and_unmeasured_values_fail_closed() -> None:
    documents = CaseDocuments.from_config(
        ROOT / "config", max_file_bytes=1048576, ids=MemoryIds(identifiers=())
    )
    case = documents.load(ROOT / "fixtures/case-document.json")
    for changes in ({"case_id": "wrong"}, {"rubric": "not visible"}, {"canary": ""}):
        with pytest.raises(BenchError):
            documents.parse_mapping({**case.document.model_dump(mode="json"), **changes})
    value = case.document.model_dump(mode="json")
    value["revealable"]["capabilities"][0]["answer"]["outcome"] = "unavailable"
    with pytest.raises(BenchError):
        documents.parse_mapping(value)


def test_label_rows_parse_strictly_and_keep_their_order() -> None:
    """`label_constraints` on its own, past the schema: every malformed row refuses the whole document."""
    row = {
        "family_id": " radiation ",
        "source": " COORDINATOR-DECISIONS D2 ",
        "expected_urgency": "emergency",
    }
    other = {"family_id": "thermal", "source": "COORDINATOR-DECISIONS D2", "expected_urgency": "act"}
    assert label_constraints({"label_constraints": [row, other]}) == (
        LabelConstraint("radiation", "COORDINATOR-DECISIONS D2", "emergency"),
        LabelConstraint("thermal", "COORDINATOR-DECISIONS D2", "act"),
    )
    assert label_constraints({"label_constraints": []}) == ()
    for rows in (
        None,
        {"0": row},
        [{**row, "note": "extra"}],
        [{**row, "family_id": "  "}],
        [{**row, "source": "  "}],
        [{**row, "expected_urgency": "Emergency"}],
        [row, {**row, "family_id": "radiation", "expected_urgency": "act"}],
    ):
        with pytest.raises(BenchError, match="^case_invalid$"):
            label_constraints({"label_constraints": rows})


def test_independent_labels_bind_only_their_family_and_cited_source() -> None:
    d2 = (LabelConstraint("radiation", "COORDINATOR-DECISIONS D2", "emergency"),)
    cited = "NASA RS53; COORDINATOR-DECISIONS D2 supplies the urgency label"

    def check(source: str, ids: tuple[str, ...], urgency: str, family: str) -> None:
        require_independent_labels(source, ids, urgency, family_id=family, constraints=d2)

    check(cited, ("RS53",), "emergency", "radiation")
    check(cited, ("RS53",), "act", "thermal")
    check("NASA RS53 only", ("RS53",), "act", "radiation")
    for source, ids, urgency in (
        (cited, ("RS53",), "act"),
        (cited, (), "emergency"),
        ("Config:warning-rules", ("RS53",), "emergency"),
        ("configuration: kit-map", ("RS53",), "emergency"),
    ):
        with pytest.raises(BenchError, match="^case_invalid$"):
            check(source, ids, urgency, "radiation")
