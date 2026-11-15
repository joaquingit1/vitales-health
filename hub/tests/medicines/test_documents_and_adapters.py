"""Configuration documents (schemas, checks M-L1 to M-L9, rule 1 content edits) and the potency engine adapter
mapping (T-72) over a recorded run of the real pharmacy pipeline."""

from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.medicines.adapters.documents import load, read_pharmacy
from crew_health.medicines.adapters.potency import PotencyWorker, summaries
from crew_health.medicines.domain.documents import DocumentsInvalid
from crew_health.medicines.domain.engine import propose

from .harness import CONFIG, REPO, SCHEMAS, build_station, config_copy, headers
from .kit import ask, condition_ids, given

FIXTURE = Path(__file__).parent / "fixtures/engine-run.json"


def _validator(directory: Path, name: str) -> Draft202012Validator:
    resources = []
    for path in sorted(directory.glob("*.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema)
        resources.append((path.name, resource))
        if isinstance(schema, dict) and "$id" in schema:
            resources.append((str(schema["$id"]), resource))
    registry: Registry = Registry().with_resources(resources)
    schema = json.loads((directory / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=registry)


@pytest.mark.trace("E32-8.4", "M-SCHEMA")
@pytest.mark.parametrize(
    ("document", "schema"),
    [
        ("treatments.json", "config-treatments.schema.json"),
        ("medicines.json", "config-medicines.schema.json"),
    ],
)
def test_the_medicines_documents_match_their_contract_schemas(document: str, schema: str) -> None:
    value = json.loads((CONFIG / document).read_text(encoding="utf-8"))
    errors = sorted(_validator(SCHEMAS, schema).iter_errors(value), key=str)
    assert errors == []


@pytest.mark.trace("E32-8.4")
def test_the_repository_configuration_passes_every_check() -> None:
    loaded = load(CONFIG, condition_ids())
    assert set(loaded.treatments.rules) == {"apap_pain", "ibu_pain", "pmz_nausea"}


@pytest.mark.trace("E32-8.4", "M-SRC")
def test_every_dose_value_cites_a_source_and_nasa_sources_come_first() -> None:
    raw = json.loads((CONFIG / "treatments.json").read_text(encoding="utf-8"))
    nasa = {s["source_id"] for s in raw["sources"] if s.get("nasa")}
    assert {"N18", "RT04", "RT22"} <= nasa
    catalog = (REPO / "docs/product/NASA-SOURCES.md").read_text(encoding="utf-8")
    for source_id in nasa:  # every NASA ID is a row of the catalog (an ID cell may list several IDs)
        assert re.search(rf"^\| (?:[A-Z0-9]+, )*{source_id}\b", catalog, re.MULTILINE), source_id


def _edit_rule(edit: str) -> object:
    def apply(treatments: dict[str, object], policy: dict[str, object]) -> None:
        rule = treatments["dose_rules"][0]["ingredients"][0]  # type: ignore[index]
        if edit == "cap_below_dose":
            rule["max_per_administration"]["value"] = 325
        elif edit == "unsourced":
            rule_raw = treatments["dose_rules"][0]  # type: ignore[index]
            rule_raw["provenance"] = rule_raw["provenance"][1:]
        elif edit == "unknown_condition":
            treatments["indications"][0]["condition_id"] = "ICL9999"  # type: ignore[index]
        elif edit == "unknown_zone":
            policy["environment_zones"]["lunar_surface"] = ["nowhere"]  # type: ignore[index]
        elif edit == "bad_unit":
            rule["dose"]["ucum"] = "mL"

    return apply


@pytest.mark.trace("E32-8.4", "M-L1", "M-L2", "M-L4", "M-L5", "M-L8")
@pytest.mark.parametrize(
    ("edit", "check"),
    [
        ("cap_below_dose", "M-L5"),
        ("unsourced", "M-SRC"),
        ("unknown_condition", "M-L8"),
        ("unknown_zone", "M-L4"),
        ("bad_unit", "M-L2"),
    ],
)
def test_an_invalid_document_is_rejected_with_its_check(tmp_path: Path, edit: str, check: str) -> None:
    directory = config_copy(tmp_path, _edit_rule(edit))  # type: ignore[arg-type]
    with pytest.raises(DocumentsInvalid) as raised:
        load(directory, condition_ids())
    assert check in {f.check for f in raised.value.findings}


@pytest.mark.trace("E32-8.4")
def test_the_module_does_not_start_on_an_invalid_document(tmp_path: Path) -> None:
    directory = config_copy(tmp_path, _edit_rule("cap_below_dose"))  # type: ignore[arg-type]
    with pytest.raises(DocumentsInvalid):
        build_station(tmp_path / "hub", config_dir=directory)


@pytest.mark.trace("E32-8.4", "AGENTS-1")
def test_a_configuration_edit_changes_the_words_with_no_code_change(tmp_path: Path) -> None:
    def words(treatments: dict[str, object], policy: dict[str, object]) -> None:
        treatments["unit_display"]["{tbl}"] = "pills"  # type: ignore[index]

    before = propose(ask("acetaminophen_325_tab", "ICL63"), given())
    directory = config_copy(tmp_path, words)
    edited = load(directory, condition_ids())
    from dataclasses import replace

    after = propose(ask("acetaminophen_325_tab", "ICL63"), replace(given(), treatments=edited.treatments))
    assert before.amount_text is not None and after.amount_text is not None
    assert "pills" in after.amount_text and "pills" not in before.amount_text
    assert after.amount == before.amount  # the dose is unchanged: only the words came from the edit


@pytest.mark.trace("T-72")
def test_the_recorded_engine_run_maps_to_lot_summaries_in_fractions() -> None:
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    validator = _validator(REPO / "contracts/pharmacy", "inventory-assessment.schema.json")
    assert sorted(validator.iter_errors(recorded["result"]), key=str) == []
    mapped = summaries("medr_1", recorded["result"])
    pmz, apap = mapped["lot-pmz-tab-001"], mapped["lot-apap-001"]
    assert pmz.state == "adjust" and pmz.adjustment_eligible
    # quantiles of the recorded run, percent of label claim: 0.025 -> 90.63, 0.5 -> 97.45, 0.975 -> 101.17
    potency = pmz.potency["promethazine"]
    rounded = tuple(round(x, 4) for x in (potency.low, potency.median, potency.high))
    assert rounded == (Decimal("0.9063"), Decimal("0.9745"), Decimal("1.0117"))
    assert apap.state == "in_specification" and not apap.adjustment_eligible
    assert pmz.engine_version == "0.1.0"


def _worker(python: Path | None, timeout: float = 5) -> PotencyWorker:
    return PotencyWorker(python, timeout_seconds=timeout, memory_limit_bytes=4 * 1024**3, memory_sample_ms=50)


@pytest.mark.trace("T-72", "DD9")
def test_an_engine_that_cannot_start_is_a_failed_run_never_an_exception(tmp_path: Path) -> None:
    bundle = read_pharmacy(CONFIG / "pharmacy")
    for python in (None, tmp_path / "no-python"):
        outcome = _worker(python).assess(bundle, b"{}")
        assert (outcome.status, outcome.error_code, outcome.result) == ("failed", "engine_unavailable", None)


@pytest.mark.trace("T-72", "DD9")
def test_an_engine_past_its_deadline_is_timed_out(tmp_path: Path) -> None:
    outcome = _worker(Path(sys.executable), timeout=0.001).assess(read_pharmacy(CONFIG / "pharmacy"), b"{}")
    assert outcome.status == "timed_out" and outcome.error_code == "timed_out"


def test_harness_headers_name_a_session() -> None:
    assert headers()["Authorization"] == "Bearer cmo"
