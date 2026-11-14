"""Tests of the foundation modules: constants, types helpers, config loading and the
generated contracts (docs/ENGINE-SPEC.md sections 1 and 2). Foundation-owned."""

import copy
import json
import math
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from pharmacy import constants as c
from pharmacy.config import (
    ConfigBundle,
    canonical_json_bytes,
    canonical_sha256,
    document_from_json,
    parse_json_text,
    reference_problems,
    schema_registry,
    schema_uri,
    schema_violations,
)
from pharmacy.contracts import calibration_reference_problems, drift, repository_problems, scenario_problems
from pharmacy.errors import ConfigLoadError, ConfigSchemaError
from pharmacy.types import (
    DegradantEstimate,
    DegradantIdentification,
    JSONObject,
    JSONValue,
    Reason,
    ScenarioDoc,
    SensorPoint,
    iso_to_seconds,
    seconds_to_iso,
    sensor_series_from_json,
    to_json,
)
from tests.conftest import cache_byte_hashes

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- constants ------------------------------------------------------------------------------
def test_gas_constant_is_exact_product() -> None:
    assert pytest.approx(8.31446261815324, rel=1e-15) == c.GAS_CONSTANT_J_PER_MOL_K


def test_g_value_conversion_matches_mds63() -> None:
    assert pytest.approx(1.0364e-7, rel=1e-4) == c.G_VALUE_MOLECULES_PER_100EV_TO_MOL_PER_J


def test_calorie_vector(vector: Callable[[str], dict[str, Any]]) -> None:
    v = vector("TV-KIN-7")
    assert v["inputs"]["kj_per_kcal"] == c.THERMOCHEMICAL_CALORIE_J


# --- time and JSON helpers -----------------------------------------------------------------------
def test_iso_round_trip() -> None:
    t = iso_to_seconds("2028-01-10T00:00:00Z")
    assert t == (date(2028, 1, 10) - date(1970, 1, 1)).days * 86400.0
    assert seconds_to_iso(t) == "2028-01-10T00:00:00.000000Z"
    assert iso_to_seconds(seconds_to_iso(t + 0.25)) == t + 0.25


@pytest.mark.parametrize("bad", ["2028-01-10T00:00:00", "2028-01-10T00:00:00+01:00"])
def test_iso_requires_utc_z(bad: str) -> None:
    with pytest.raises(ValueError, match="UTC"):
        iso_to_seconds(bad)


def test_seconds_to_iso_rejects_nan() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        seconds_to_iso(math.nan)


def test_to_json_dataclass_and_rejects_nan() -> None:
    assert to_json(SensorPoint("2028-01-10T00:00:00Z", 1.0, 2.5)) == {
        "t_utc": "2028-01-10T00:00:00Z",
        "t_s": 1.0,
        "value": 2.5,
    }
    assert to_json((1, "a", None, True, {"k": [1.5]})) == [1, "a", None, True, {"k": [1.5]}]
    with pytest.raises(ValueError, match="non-finite"):
        to_json(SensorPoint("x", math.inf, 0.0))
    with pytest.raises(ValueError, match="keys"):
        to_json({1: 2})
    with pytest.raises(ValueError, match="cannot convert"):
        to_json(object())


def test_sensor_series_from_json() -> None:
    series = sensor_series_from_json(
        {
            "series_id": "s",
            "sensor_id": "x",
            "zone_id": "z",
            "quantity": "temperature_k",
            "unit": "K",
            "points": [{"t_utc": "2028-01-10T00:00:00Z", "t_s": 1831161600, "value": 295}],
        }
    )
    assert series.points[0].value == 295.0
    assert isinstance(series.points[0].t_s, float)
    with pytest.raises(ValueError, match="quantity"):
        sensor_series_from_json(
            {
                "series_id": "s",
                "sensor_id": "x",
                "zone_id": "z",
                "quantity": "pressure",
                "unit": "Pa",
                "points": [],
            }
        )


# --- canonical JSON and strict parsing ------------------------------------------------------------
def test_canonical_json_is_order_independent() -> None:
    a: JSONValue = {"b": 1, "a": [1.5, "é"]}
    b: JSONValue = {"a": [1.5, "é"], "b": 1}
    assert canonical_json_bytes(a) == canonical_json_bytes(b) == '{"a":[1.5,"é"],"b":1}'.encode()
    assert canonical_sha256(a) == canonical_sha256(b)


def test_canonical_json_rejects_nan() -> None:
    with pytest.raises(ConfigLoadError):
        canonical_json_bytes({"x": math.nan})


@pytest.mark.parametrize("text", ['{"a": NaN}', '{"a": 1, "a": 2}', "{"])
def test_strict_parse_rejects(text: str) -> None:
    with pytest.raises(ConfigLoadError):
        parse_json_text(text, "<test>")


# --- schemas and the bundle -------------------------------------------------------------------------
def test_schemas_match_types() -> None:
    assert drift() == []


def test_repository_is_consistent() -> None:
    assert repository_problems() == []


def test_team_default_requires_owner(bundle: ConfigBundle) -> None:
    doc = cast(dict[str, Any], copy.deepcopy(bundle.documents["engine-settings"].data))
    doc["monte_carlo"]["max_batches"]["to_be_set_by"] = None
    errors = schema_violations(cast(JSONValue, doc), "pharmacy/engine-settings.schema.json")
    assert any("max_batches" in e for e in errors)


def test_sourced_requires_source_ids(bundle: ConfigBundle) -> None:
    doc = cast(dict[str, Any], copy.deepcopy(bundle.documents["engine-settings"].data))
    doc["reference_conditions"]["temperature_k"]["source_ids"] = []
    with pytest.raises(ConfigSchemaError):
        document_from_json(cast(JSONValue, doc), "<test>")


def test_closed_objects(bundle: ConfigBundle) -> None:
    doc = cast(dict[str, Any], copy.deepcopy(bundle.documents["packages"].data))
    doc["packages"][0]["colour"] = "red"
    assert schema_violations(cast(JSONValue, doc), "pharmacy/packages.schema.json")


def test_fixed_prior_needs_zero_sigma(bundle: ConfigBundle) -> None:
    doc = cast(dict[str, Any], copy.deepcopy(bundle.documents["storage-zones"].data))
    doc["zones"][0]["rate_multiplier"]["sigma"] = 0.1
    assert schema_violations(cast(JSONValue, doc), "pharmacy/storage-zones.schema.json")


def test_unknown_source_is_a_reference_problem(bundle: ConfigBundle) -> None:
    docs = dict(bundle.documents)
    data = cast(dict[str, Any], copy.deepcopy(docs["packages"].data))
    data["packages"][0]["source_ids"] = ["MDP99"]
    docs["packages"] = document_from_json(cast(JSONValue, data), "<test>")
    problems = reference_problems(ConfigBundle(documents=docs))
    assert any("MDP99" in p for p in problems)


def test_document_schema_member_must_match(bundle: ConfigBundle) -> None:
    with pytest.raises(ConfigLoadError, match="expected schema"):
        document_from_json(bundle.documents["lots"].data, "<test>", "pharmacy/formulary.schema.json")


def test_pins_sorted(bundle: ConfigBundle) -> None:
    ids = [p.document_id for p in bundle.pins()]
    assert ids == sorted(ids)
    assert len(ids) == 11


def test_scenario_semantics_catch_unknown_lot(bundle: ConfigBundle, demo_scenario: dict[str, Any]) -> None:
    doc = copy.deepcopy(demo_scenario)
    doc["assays"][0]["lot_id"] = "lot-nope"
    problems = scenario_problems(cast(ScenarioDoc, doc), bundle)
    assert any("lot not in the scenario" in p for p in problems)


def test_vectors_have_unique_ids() -> None:
    ids: list[str] = []
    for path in sorted((REPO_ROOT / "tests" / "data" / "vectors").glob("*.json")):
        ids += [v["vector_id"] for v in json.loads(path.read_text(encoding="utf-8"))["vectors"]]
    assert len(ids) == len(set(ids))


def _changed_bundle(bundle: ConfigBundle, member: str, data: dict[str, Any]) -> ConfigBundle:
    documents = dict(bundle.documents)
    documents[member] = document_from_json(cast(JSONValue, data), "<test>")
    return ConfigBundle(documents)


def _calibration() -> dict[str, Any]:
    """Synthetic derivation record; no scientific calibration values are asserted here."""
    return {
        "method": "foundation schema test",
        "vector_id": "V1",
        "rows": [1, 3, 9, 11, 19, 21, 23, 25, 27, 31, 35],
        "statistics": [{"name": "n_products", "value": 11, "unit": "1"}],
        "engine_version": "0.1.0",
        "basis": "derived",
        "source_ids": ["MDP01", "MDP02"],
        "to_be_set_by": None,
        "note": "Synthetic metadata used only to test the contract.",
    }


@pytest.mark.parametrize("member", ["reaction-classes", "storage-zones"])
def test_calibration_record_on_both_owners(bundle: ConfigBundle, member: str) -> None:
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents[member].data))
    if member == "reaction-classes":
        data["total_rate_priors"][0]["calibration_provenance"] = _calibration()
    else:
        data["zones"][1]["rate_multiplier_calibration"] = _calibration()
    assert reference_problems(_changed_bundle(bundle, member, data)) == []


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("method", ""),
        ("rows", []),
        ("rows", [1, 1]),
        ("rows", [0]),
        ("rows", [1.5]),
        ("statistics", []),
        ("engine_version", "unknown"),
        ("source_ids", []),
        ("note", ""),
    ],
)
def test_calibration_provenance_rules(bundle: ConfigBundle, field: str, bad: Any) -> None:
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents["reaction-classes"].data))
    record = _calibration()
    record[field] = bad
    data["total_rate_priors"][0]["calibration_provenance"] = record
    assert schema_violations(cast(JSONValue, data), "pharmacy/reaction-classes.schema.json")


def test_calibration_sources_statistics_and_reference_agreement(bundle: ConfigBundle) -> None:
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents["reaction-classes"].data))
    record = _calibration()
    record["source_ids"] = ["MDP99"]
    record["statistics"] *= 2
    record["rows"] = [1]
    data["total_rate_priors"][0]["calibration_provenance"] = record
    problems = reference_problems(_changed_bundle(bundle, "reaction-classes", data))
    assert any("MDP99" in p for p in problems)
    assert any("duplicate id 'n_products'" in p for p in problems)
    assert any("disagrees" in p for p in problems)
    record["basis"] = "team_default"
    record["source_ids"] = []
    assert schema_violations(cast(JSONValue, data), "pharmacy/reaction-classes.schema.json")


def test_calibration_vector_and_row_references(bundle: ConfigBundle) -> None:
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents["reaction-classes"].data))
    data["total_rate_priors"][0]["calibration"]["rows"] = [999]
    data["total_rate_priors"][1]["calibration"]["vector_id"] = "V-missing"
    vectors: dict[str, JSONObject] = {}
    for path in (REPO_ROOT / "tests/data/vectors").glob("*.json"):
        for vector in json.loads(path.read_text())["vectors"]:
            vectors[vector["vector_id"]] = vector
    problems = calibration_reference_problems(_changed_bundle(bundle, "reaction-classes", data), vectors)
    assert any("unknown calibration row 999" in p for p in problems)
    assert any("unknown calibration vector 'V-missing'" in p for p in problems)


def test_prior_reference_conditions_and_suspension(bundle: ConfigBundle) -> None:
    priors = {p["dose_form_family"]: p for p in bundle.reaction_classes["total_rate_priors"]}
    for prior in priors.values():
        assert prior["temperature_k"]["value"] == 295.15
        assert prior["temperature_sd_k"]["value"] == 1.5
        assert prior["rh_percent"]["value"] == 40
        assert prior["rh_sd_percent"]["value"] == 2
        for field in ("temperature_k", "temperature_sd_k"):
            item = cast(dict[str, Any], prior)[field]
            assert item["basis"] == "team_default"
            assert item["to_be_set_by"] == "chemistry lead"
            assert item["source_ids"] == ["MDP03"]
        for field in ("rh_percent", "rh_sd_percent"):
            item = cast(dict[str, Any], prior)[field]
            assert item["basis"] == "sourced"
            assert item["source_ids"] == ["MDP01", "MDP02"]
    for field in ("temperature_k", "temperature_sd_k", "rh_percent", "rh_sd_percent"):
        assert (
            cast(dict[str, Any], priors["suspension"])[field]
            == cast(dict[str, Any], priors["solution"])[field]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("temperature_k", 0), ("temperature_sd_k", -1), ("rh_percent", 101), ("rh_sd_percent", -1)],
)
def test_prior_reference_domains(bundle: ConfigBundle, field: str, value: float) -> None:
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents["reaction-classes"].data))
    data["total_rate_priors"][0][field]["value"] = value
    assert schema_violations(cast(JSONValue, data), "pharmacy/reaction-classes.schema.json")


@pytest.mark.parametrize(
    ("group", "field", "value"),
    [
        ("numerics", "worker_threads", -1),
        ("numerics", "worker_threads", 1.5),
        ("numerics", "compression_relative_tolerance", 0),
        ("validated_range", "temperature_below_min_margin_k", -1),
    ],
)
def test_new_setting_domains(bundle: ConfigBundle, group: str, field: str, value: float) -> None:
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents["engine-settings"].data))
    data[group][field]["value"] = value
    assert schema_violations(cast(JSONValue, data), "pharmacy/engine-settings.schema.json")


def test_new_settings_provenance(bundle: ConfigBundle) -> None:
    settings = bundle.settings
    for item in (
        settings["numerics"]["worker_threads"],
        settings["numerics"]["compression_relative_tolerance"],
        settings["validated_range"]["temperature_below_min_margin_k"],
    ):
        assert item["basis"] == "team_default"
        assert item["to_be_set_by"]
        assert item["note"]
    assert settings["numerics"]["worker_threads"]["value"] == 0
    assert settings["numerics"]["compression_relative_tolerance"]["value"] == 1e-7
    assert settings["validated_range"]["temperature_below_min_margin_k"]["value"] == 10


def test_measured_degradant_reference_scope(bundle: ConfigBundle) -> None:
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents["reaction-classes"].data))
    anchor = data["classes"][0]["anchors"][0]
    anchor["measured_degradant_ids"] = ["salicylic_acid"]
    assert reference_problems(_changed_bundle(bundle, "reaction-classes", data)) == []
    anchor["measured_degradant_ids"] = ["4_aminophenol"]
    assert any(
        "unrelated degradant" in p
        for p in reference_problems(_changed_bundle(bundle, "reaction-classes", data))
    )
    anchor["measured_degradant_ids"] = ["salicylic_acid", "salicylic_acid"]
    assert schema_violations(cast(JSONValue, data), "pharmacy/reaction-classes.schema.json")


def test_anchor_storage_references_and_duration(bundle: ConfigBundle) -> None:
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents["reaction-classes"].data))
    anchor = data["classes"][0]["anchors"][0]
    assert anchor["storage_zone_id"] == "station_kit"
    assert anchor["duration_days"]["value"] == 550
    anchor["storage_zone_id"] = "unknown-zone"
    anchor["package_id"] = "unknown-package"
    problems = reference_problems(_changed_bundle(bundle, "reaction-classes", data))
    assert any("unknown storage zone" in p for p in problems)
    assert any("unknown package" in p for p in problems)
    anchor["duration_days"] = None
    assert schema_violations(cast(JSONValue, data), "pharmacy/reaction-classes.schema.json")


def test_anchor_context_provenance(bundle: ConfigBundle) -> None:
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents["reaction-classes"].data))
    anchor = data["classes"][0]["anchors"][0]
    anchor["storage_context"]["basis"] = "team_default"
    anchor["storage_context"]["to_be_set_by"] = None
    assert schema_violations(cast(JSONValue, data), "pharmacy/reaction-classes.schema.json")
    anchor["storage_context"]["to_be_set_by"] = "chemistry lead"
    anchor["duration_days"]["source_ids"] = []
    assert schema_violations(cast(JSONValue, data), "pharmacy/reaction-classes.schema.json")


def test_measured_model_degradant_references(bundle: ConfigBundle) -> None:
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents["stability-models"].data))
    model = copy.deepcopy(data["models"][0])
    data["models"].append(model)
    model.update(
        model_id="aspirin_measured_test",
        scope="product",
        product_id="aspirin_325_tab",
        ingredient_id="aspirin",
        measured_degradant_ids=["salicylic_acid"],
    )
    assert reference_problems(_changed_bundle(bundle, "stability-models", data)) == []
    model["measured_degradant_ids"] = ["4_aminophenol"]
    assert any(
        "unrelated degradant" in p
        for p in reference_problems(_changed_bundle(bundle, "stability-models", data))
    )


def test_identification_and_warning_output_schema() -> None:
    registry = schema_registry()

    def errors(value: JSONValue, schema: str, name: str) -> list[Any]:
        validator = Draft202012Validator(
            {"$ref": schema_uri(f"pharmacy/{schema}.schema.json") + f"#/$defs/{name}"}, registry=registry
        )
        return list(validator.iter_errors(value))

    estimate = DegradantEstimate(
        "d", "Degradant", "p", "percent_of_label_api_mass", (), 0, None, None, "unknown", False
    )
    assert not estimate.identification.identified
    assert not errors(to_json(estimate), "potency-estimate", "DegradantEstimate")
    good = DegradantIdentification(True, "anchor", "anchor-1", ("MDP01",))
    assert not errors(to_json(good), "potency-estimate", "DegradantIdentification")
    assert errors(to_json(DegradantIdentification(True)), "potency-estimate", "DegradantIdentification")
    reason = Reason("degradant_unidentified_above_limit", "warning", None, "Inspect or assay.")
    assert not errors(to_json(reason), "lot-decision", "Reason")
    bad = cast(JSONObject, to_json(reason))
    bad["severity"] = "blocking"
    assert errors(bad, "lot-decision", "Reason")


def test_q3b_explicit_precision_and_provenance(bundle: ConfigBundle) -> None:
    q3b = bundle.settings["q3b"]
    assert [b["percent"]["decimals"] for b in q3b["reporting"]] == [1, 2]
    for bands in (q3b["identification"], q3b["qualification"]):
        assert [b["percent"]["decimals"] for b in bands] == [1, 1, 1, 2]
        for band in bands:
            for threshold in (band["percent"], band["tdi"]):
                if threshold is not None:
                    assert threshold["basis"] == "sourced"
                    assert threshold["source_ids"] == ["MDS04"]
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents["engine-settings"].data))
    del data["q3b"]["identification"][-1]["percent"]["decimals"]
    assert schema_violations(cast(JSONValue, data), "pharmacy/engine-settings.schema.json")


@pytest.mark.parametrize("decimals", [-1, 1.5])
def test_q3b_bad_decimals(bundle: ConfigBundle, decimals: float) -> None:
    data = cast(dict[str, Any], copy.deepcopy(bundle.documents["engine-settings"].data))
    data["q3b"]["identification"][-1]["percent"]["decimals"] = decimals
    assert schema_violations(cast(JSONValue, data), "pharmacy/engine-settings.schema.json")


def test_cache_fixture_isolated_and_hashes_detect_byte_changes(
    isolated_xtb_cache: Path, tmp_path: Path
) -> None:
    assert Path(os.environ["PHARMACY_XTB_CACHE_DIR"]) == isolated_xtb_cache
    assert isolated_xtb_cache != REPO_ROOT / "data/pharmacy/xtb-cache"
    copied = cache_byte_hashes(isolated_xtb_cache)
    for name, digest in cache_byte_hashes(REPO_ROOT / "data/pharmacy/xtb-cache").items():
        assert copied[name] == digest
    sample = tmp_path / "cache"
    sample.mkdir()
    entry = sample / "entry.json"
    entry.write_bytes(b'{"value":1}\n')
    before = cache_byte_hashes(sample)
    entry.write_bytes(b'{"value":1} \n')
    assert cache_byte_hashes(sample) != before
    entry.unlink()
    assert cache_byte_hashes(sample) != before
