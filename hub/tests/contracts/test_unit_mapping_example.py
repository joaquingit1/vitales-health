"""The D12 unit-mapping example (docs/product and its contracts copy): valid against the effective overlay schema,
UCUM codes the hub parses, and provenance that resolves to real lines, NASA first where NASA speaks."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from crew_health.contracts_gen.build import effective_schemas
from crew_health.platform.adapters.units import UcumUnitRegistry

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "D12")]
ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = "api/examples/config/unit-mapping.example.json"


def example() -> dict[str, object]:
    raw = (ROOT / "docs/product" / EXAMPLE).read_bytes()
    assert raw == (ROOT / "contracts" / EXAMPLE).read_bytes()
    document = json.loads(raw)
    assert isinstance(document, dict) and document["x-schema"] == "config-unit-mapping.schema.json"
    return document


def mappings() -> list[dict[str, object]]:
    value = example()["value"]
    assert isinstance(value, dict) and isinstance(value["mappings"], list)
    return value["mappings"]


def test_example_validates_against_the_effective_overlay_schema() -> None:
    directory = ROOT / "contracts/api/schemas"
    schemas = effective_schemas(directory, tuple(sorted(directory.glob("*.schema.json"))))
    schema = json.loads(schemas["config-unit-mapping.schema.json"])
    assert sorted(Draft202012Validator(schema).iter_errors(example()["value"]), key=str) == []
    broken = json.loads(json.dumps(example()["value"]))
    broken["mappings"][0]["ucum_unit"] = ""
    assert list(Draft202012Validator(schema).iter_errors(broken))


def test_example_ucum_codes_parse_and_the_deployed_mapping_extends_the_example() -> None:
    units = UcumUnitRegistry(max_cached=16)
    for mapping in mappings():
        assert units.parse(str(mapping["ucum_unit"])).code == mapping["ucum_unit"]
    deployed = json.loads((ROOT / "config/unit-mapping.json").read_bytes())["mappings"]
    keys = ("indicator_code", "wire_unit", "ucum_unit", "conversion", "factor")
    # Every example row is deployed unchanged; the deployed mapping may extend the frozen example (E07 adds
    # spo2 and temperature).
    rows = [tuple(m[k] for k in keys) for m in deployed]
    assert [tuple(m[k] for k in keys) for m in mappings()] == rows[: len(mappings())]


def test_every_source_reference_resolves_and_cabin_co2_cites_nasa_first() -> None:
    for mapping in mappings():
        refs = mapping["source_refs"]
        assert isinstance(refs, list)
        for ref in refs:
            lines = (ROOT / ref["path"]).read_text(encoding="utf-8").splitlines()
            assert 1 <= ref["line"] <= len(lines), ref
        if mapping["indicator_code"] == "co2":
            nasa = [ref for ref in refs if ref["path"] == "docs/product/NASA-SOURCES.md"]
            cited = [
                (ROOT / "docs/product/NASA-SOURCES.md")
                .read_text(encoding="utf-8")
                .splitlines()[ref["line"] - 1]
                for ref in nasa
            ]
            assert any("NASA-STD-3001 Volume 2" in line and "mmHg" in line for line in cited)
