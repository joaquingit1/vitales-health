"""JSON Schemas generated from ``pharmacy.types`` (docs/ENGINE-SPEC.md section 2.4).

The TypedDicts and dataclasses in ``pharmacy.types`` are the single source of truth for
the shape of every configuration document, input and output. This module turns them into
JSON Schema 2020-12 documents (every object closed with ``additionalProperties: false``,
required members preserved, nullable members spelled ``X | None``) and adds the value rules
that types cannot express (identifier, timestamp and hash patterns, provenance rules,
distribution rules). ``contracts/pharmacy/*.schema.json`` are the output of
``python -m pharmacy.contracts --write``; a unit test fails if they drift from the types.

``test-vectors.schema.json`` is written by hand and is not generated.
"""

import dataclasses
import json
import sys
import types as pytypes
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, TypeAliasType, cast, get_args, get_origin, get_type_hints, is_typeddict

from pharmacy import types as pt
from pharmacy.config import (
    DEFAULT_SCHEMA_DIR,
    REPO_ROOT,
    SCHEMA_BASE_URI,
    ConfigBundle,
    load_bundle,
    load_document,
    load_json,
    schema_violations,
)
from pharmacy.errors import ConfigError
from pharmacy.types import JSONObject, JSONValue

DRAFT = "https://json-schema.org/draft/2020-12/schema"
COMMON = "pharmacy/common.schema.json"

ID_PATTERN = "^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
SOURCE_ID_PATTERN = "^(MDS|MDP|MDD|MDX)[0-9]{2,3}$"
UTC_PATTERN = "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,9})?Z$"
SHA256_PATTERN = "^[0-9a-f]{64}$"
SEED_PATTERN = "^[0-9a-f]{32}$"
VERSION_PATTERN = "^[0-9]+\\.[0-9]+\\.[0-9]+$"

COMMON_TYPES: tuple[type, ...] = (
    pt.SourcedValue,
    pt.Prior,
    pt.Provenance,
    pt.ValidatedRangeDoc,
    pt.CalibrationProvenance,
)
"""Types emitted once in common.schema.json and referenced from every other schema."""

DOCUMENTS: Mapping[str, tuple[type, str, bool]] = {
    "sources": (
        pt.SourcesDoc,
        "Source register: every source ID any document cites.",
        True,
    ),
    "engine-settings": (
        pt.EngineSettingsDoc,
        "Engine-wide settings (reference conditions, Monte Carlo, decision, update, forecast).",
        True,
    ),
    "reaction-classes": (
        pt.ReactionClassesDoc,
        "Reaction classes with SMARTS, rate-law priors, anchors and degradants.",
        True,
    ),
    "lfer-constants": (
        pt.LferConstantsDoc,
        "Hammett, Taft and Evans-Polanyi constants for the computed layer.",
        True,
    ),
    "radiation-chemistry": (
        pt.RadiationChemistryDoc,
        "Radiolysis yields and upper-bound sensitivities.",
        True,
    ),
    "packages": (
        pt.PackagesDoc,
        "Packages and their moisture and light models.",
        True,
    ),
    "storage-zones": (
        pt.StorageZonesDoc,
        "Storage zones, their sensors, gap policy and worst-case bounds.",
        True,
    ),
    "formulary": (
        pt.FormularyDoc,
        "Products, ingredients, specifications, degradants and adjustment policy.",
        True,
    ),
    "stability-models": (
        pt.StabilityModelsDoc,
        "Measured stability models fitted to reproduced published data.",
        True,
    ),
    "environment-profiles": (
        pt.EnvironmentProfilesDoc,
        "Environment profiles and event templates for the simulator.",
        True,
    ),
    "lots": (
        pt.LotsDoc,
        "Lots in inventory with custody and package history.",
        True,
    ),
    "scenario": (
        pt.ScenarioDoc,
        "Scenario fixture: mission, zones, scripted events, observations and expectations.",
        True,
    ),
    "inputs": (
        pt.EngineInputsDoc,
        "Runtime inputs: sensor series, assays and inspections up to as_of.",
        True,
    ),
    "potency-estimate": (
        pt.PotencyEstimate,
        "Engine output: remaining active ingredient of one ingredient of one lot.",
        False,
    ),
    "forecast": (
        pt.Forecast,
        "Engine output: out-of-specification forecast for one ingredient of one lot.",
        False,
    ),
    "lot-decision": (
        pt.LotDecision,
        "Engine output: decision for one lot.",
        False,
    ),
    "inventory-assessment": (
        pt.InventoryAssessment,
        "Engine output: every lot assessed at one as_of, ordered.",
        False,
    ),
}
"""Schema name to (root type, title, is a config-style document with a ``schema`` member)."""


def _string_rule(field: str) -> JSONObject:
    if field.endswith("_utc"):
        return {"type": "string", "pattern": UTC_PATTERN}
    if field == "sha256" or field.endswith("_sha256"):
        return {"type": "string", "pattern": SHA256_PATTERN}
    if field == "seed_hex":
        return {"type": "string", "pattern": SEED_PATTERN}
    if field in {"version", "engine_version"}:
        return {"type": "string", "pattern": VERSION_PATTERN}
    if field.endswith("_id") or field == "manufacturer_lot":
        return {"type": "string", "pattern": ID_PATTERN}
    if field.endswith("smiles") or field in {"name", "citation", "unit"}:
        return {"type": "string", "minLength": 1}
    return {"type": "string"}


def _number_rule(field: str) -> JSONObject:
    rule: JSONObject = {"type": "number"}
    if field in {"probability", "coverage_probability", "quantile_probability", "share_nominal"}:
        rule.update({"minimum": 0, "maximum": 1})
    elif field.endswith("_k") and "ln_k" not in field and "sd" not in field:
        rule["exclusiveMinimum"] = 0
    elif field.endswith(("_s", "_gy", "_lux_h", "_w_h_per_m2")):
        rule["minimum"] = 0
    return rule


class _Builder:
    def __init__(self, schema_name: str) -> None:
        self.schema_name = schema_name
        self.defs: dict[str, JSONValue] = {}

    def ref(self, cls: type) -> JSONObject:
        name = cls.__name__
        if cls in COMMON_TYPES and self.schema_name != "common":
            return {"$ref": f"common.schema.json#/$defs/{name}"}
        if name not in self.defs:
            self.defs[name] = {}  # placeholder breaks recursion
            self.defs[name] = self.object_schema(cls)
        return {"$ref": f"#/$defs/{name}"}

    def object_schema(self, cls: type) -> JSONObject:
        hints = get_type_hints(cls, include_extras=True)
        names = [f.name for f in dataclasses.fields(cls)] if dataclasses.is_dataclass(cls) else list(hints)
        properties: dict[str, JSONValue] = {name: self.field_schema(name, hints[name]) for name in names}
        out: JSONObject = {"type": "object", "additionalProperties": False}
        doc = cls.__doc__
        if doc and not doc.startswith(f"{cls.__name__}("):
            out["description"] = " ".join(doc.split())
        if dataclasses.is_dataclass(cls):
            required = [
                f.name
                for f in dataclasses.fields(cls)
                if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
            ]
            for f in dataclasses.fields(cls):
                if f.default is not dataclasses.MISSING:
                    cast(JSONObject, properties[f.name])["default"] = pt.to_json(f.default)
        else:
            required = [name for name in names if get_origin(hints[name]) is not typing.NotRequired]
        out["required"] = cast(JSONValue, required)
        out["properties"] = properties
        rules = _class_rules(names) + _specific_rules(cls, properties)
        if rules:
            out["allOf"] = cast(JSONValue, rules)
        return out

    def field_schema(self, field: str, annotation: object) -> JSONValue:
        if field == "source_ids":
            return {"type": "array", "items": {"$ref": self._common_ref("SourceId")}}
        return self.type_schema(field, annotation)

    def _common_ref(self, name: str) -> str:
        return f"#/$defs/{name}" if self.schema_name == "common" else f"common.schema.json#/$defs/{name}"

    def type_schema(self, field: str, annotation: object) -> JSONValue:
        if isinstance(annotation, TypeAliasType):
            return {}
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin is typing.NotRequired:
            return self.type_schema(field, args[0])
        if origin is Literal:
            return {"enum": cast(JSONValue, list(args))}
        if origin in (typing.Union, pytypes.UnionType):
            non_null = [a for a in args if a is not type(None)]
            nullable = len(non_null) < len(args)
            simple = {float: "number", int: "integer", str: "string", bool: "boolean"}
            if all(a in simple for a in non_null) and len(non_null) > 1:
                kinds = [simple[cast(type, a)] for a in non_null] + (["null"] if nullable else [])
                return {"type": cast(JSONValue, kinds)}
            inner: JSONValue
            if len(non_null) == 1:
                inner = self.type_schema(field, non_null[0])
            else:
                inner = {"anyOf": [self.type_schema(field, a) for a in non_null]}
            return {"anyOf": [inner, {"type": "null"}]} if nullable else inner
        if origin in (list, tuple):
            if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
                items = [self.type_schema(field, a) for a in args]
                return {"type": "array", "prefixItems": items, "minItems": len(items), "maxItems": len(items)}
            item_field = field[:-1] if field.endswith("s") else field
            return {"type": "array", "items": self.type_schema(item_field, args[0])}
        if annotation is bool:
            return {"type": "boolean"}
        if annotation is int:
            return {"type": "integer"}
        if annotation is float:
            return _number_rule(field)
        if annotation is str:
            return _string_rule(field)
        if isinstance(annotation, type) and (
            is_typeddict(annotation) or dataclasses.is_dataclass(annotation)
        ):
            return self.ref(annotation)
        raise TypeError(f"no schema mapping for {field}: {annotation!r}")


def _class_rules(names: list[str]) -> list[JSONValue]:
    rules: list[JSONValue] = []
    present = set(names)
    if {"basis", "to_be_set_by"} <= present:
        rules.append(
            {
                "if": {"properties": {"basis": {"const": "team_default"}}},
                "then": {"properties": {"to_be_set_by": {"type": "string", "minLength": 1}}},
            }
        )
    if {"basis", "source_ids"} <= present:
        rules.append(
            {
                "if": {"properties": {"basis": {"enum": ["sourced", "derived"]}}},
                "then": {"properties": {"source_ids": {"minItems": 1}}},
            }
        )
    if {"family", "mu", "sigma", "lower", "upper"} <= present:
        rules += [
            {
                "if": {"properties": {"family": {"const": "fixed"}}},
                "then": {
                    "properties": {
                        "sigma": {"const": 0},
                        "lower": {"type": "null"},
                        "upper": {"type": "null"},
                    }
                },
                "else": {"properties": {"sigma": {"exclusiveMinimum": 0}}},
            },
            {
                "if": {"properties": {"family": {"enum": ["normal", "lognormal"]}}},
                "then": {"properties": {"lower": {"type": "null"}, "upper": {"type": "null"}}},
            },
            {
                "if": {"properties": {"family": {"const": "truncated_normal"}}},
                "then": {
                    "anyOf": [
                        {"properties": {"lower": {"type": "number"}}},
                        {"properties": {"upper": {"type": "number"}}},
                    ]
                },
            },
        ]
    return rules


def _specific_rules(cls: type, properties: dict[str, JSONValue]) -> list[JSONValue]:
    """Value constraints whose meaning depends on the containing type."""
    rules: list[JSONValue] = []

    def constrain(name: str, rule: JSONObject) -> None:
        properties[name] = {"allOf": [properties[name], rule]}

    def sourced_number(name: str, rule: JSONObject, unit: str) -> None:
        constrain(name, {"properties": {"value": rule, "unit": {"const": unit}}})

    if cls is pt.NumericsSettings:
        sourced_number("worker_threads", {"type": "integer", "minimum": 0}, "1")
        sourced_number("compression_relative_tolerance", {"exclusiveMinimum": 0}, "1")
    if cls is pt.ValidatedRangeSettings:
        sourced_number("temperature_below_min_margin_k", {"minimum": 0}, "K")
    if cls in (pt.TotalRatePrior, pt.Anchor):
        sourced_number("temperature_k", {"exclusiveMinimum": 0}, "K")
        sourced_number("temperature_sd_k", {"minimum": 0}, "K")
        sourced_number("rh_percent", {"minimum": 0, "maximum": 100}, "percent")
    if cls is pt.TotalRatePrior:
        sourced_number("rh_sd_percent", {"minimum": 0}, "percent")
    if cls is pt.Anchor:
        sourced_number("duration_days", {"exclusiveMinimum": 0}, "day")
        rules.append(
            {
                "if": {"properties": {"package_id": {"type": "string"}}},
                "then": {"properties": {"duration_days": {"type": "object"}}},
            }
        )
    if cls in (pt.Anchor, pt.StabilityModelDoc):
        constrain(
            "measured_degradant_ids",
            {"uniqueItems": True, "items": {"type": "string", "pattern": ID_PATTERN}},
        )
        cast(JSONObject, properties["measured_degradant_ids"])["default"] = []
    if cls in (pt.TotalRatePrior, pt.StorageZoneDoc):
        field = "calibration_provenance" if cls is pt.TotalRatePrior else "rate_multiplier_calibration"
        cast(JSONObject, properties[field])["default"] = None
    if cls is pt.CalibrationProvenance:
        constrain("method", {"minLength": 1, "pattern": "\\S"})
        constrain("note", {"minLength": 1, "pattern": "\\S"})
        constrain("rows", {"minItems": 1, "uniqueItems": True, "items": {"type": "integer", "minimum": 1}})
        constrain("statistics", {"minItems": 1})
    if cls is pt.Q3bThreshold:
        constrain("decimals", {"minimum": 0})
        constrain("value", {"exclusiveMinimum": 0})
        constrain("unit", {"enum": ["percent", "ug", "mg"]})
    if cls is pt.Q3bSettings:
        for name in ("reporting", "identification", "qualification"):
            constrain(name, {"minItems": 1})
    if cls is pt.Q3bBand:
        constrain("percent", {"properties": {"unit": {"const": "percent"}}})
        constrain("tdi", {"properties": {"unit": {"enum": ["ug", "mg"]}}})
        constrain(
            "dose_upper_mg", {"properties": {"value": {"exclusiveMinimum": 0}, "unit": {"const": "mg"}}}
        )
    if cls is pt.DegradantIdentification:
        rules.append(
            {
                "if": {"required": ["identified"], "properties": {"identified": {"const": True}}},
                "then": {
                    "required": ["evidence_kind", "evidence_id", "source_ids"],
                    "properties": {
                        "evidence_kind": {"enum": ["measured_model", "anchor"]},
                        "evidence_id": {"type": "string"},
                        "source_ids": {"minItems": 1},
                    },
                },
                "else": {
                    "properties": {
                        "evidence_kind": {"type": "null"},
                        "evidence_id": {"type": "null"},
                        "source_ids": {"maxItems": 0},
                    }
                },
            }
        )
    if cls is pt.Reason:
        rules.append(
            {
                "if": {"properties": {"code": {"const": "degradant_unidentified_above_limit"}}},
                "then": {"properties": {"severity": {"const": "warning"}}},
            }
        )
    return rules


def _common_schema() -> JSONObject:
    builder = _Builder("common")
    for cls in COMMON_TYPES:
        builder.ref(cls)
    defs: dict[str, JSONValue] = {
        "SourceId": {"type": "string", "pattern": SOURCE_ID_PATTERN},
        "Identifier": {"type": "string", "pattern": ID_PATTERN},
        "UtcTimestamp": {"type": "string", "pattern": UTC_PATTERN},
        "Sha256": {"type": "string", "pattern": SHA256_PATTERN},
    }
    defs.update(builder.defs)
    return {
        "$schema": DRAFT,
        "$id": SCHEMA_BASE_URI + COMMON,
        "title": "Shared definitions",
        "description": (
            "Building blocks referenced by the other pharmacy schemas: provenance-carrying values, "
            "parameter distributions, validated ranges and identifier patterns."
        ),
        "$defs": defs,
    }


def _document_schema(name: str, root: type, title: str, has_schema_member: bool) -> JSONObject:
    builder = _Builder(name)
    body = builder.object_schema(root)
    props = cast(dict[str, JSONValue], body["properties"])
    if has_schema_member:
        props["schema"] = {"const": f"pharmacy/{name}.schema.json"}
    schema: JSONObject = {
        "$schema": DRAFT,
        "$id": f"{SCHEMA_BASE_URI}pharmacy/{name}.schema.json",
        "title": title,
    }
    schema.update(body)
    if builder.defs:
        schema["$defs"] = dict(sorted(builder.defs.items()))
    return schema


def generate_schemas() -> dict[str, JSONObject]:
    """Every generated schema keyed by file name (``<name>.schema.json``)."""
    out: dict[str, JSONObject] = {"common.schema.json": _common_schema()}
    for name, (root, title, has_schema_member) in DOCUMENTS.items():
        out[f"{name}.schema.json"] = _document_schema(name, root, title, has_schema_member)
    return out


def render(schema: JSONObject) -> str:
    """Stable text form of a schema file (two-space indent, trailing newline)."""
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def drift(schema_dir: Path = DEFAULT_SCHEMA_DIR) -> list[str]:
    """File names whose content differs from the generated schemas (empty when in sync)."""
    stale = []
    for file_name, schema in generate_schemas().items():
        path = schema_dir / file_name
        if not path.exists() or path.read_text(encoding="utf-8") != render(schema):
            stale.append(file_name)
    return stale


def scenario_problems(scenario: pt.ScenarioDoc, bundle: ConfigBundle) -> list[str]:
    """Semantic checks of a schema-valid scenario fixture against a bundle (spec 13.1.1):
    zones, profiles, sensors, lots, templates and ingredients exist; sensors belong to their
    zone; every time lies inside the mission; observations reference scenario lots."""
    problems: list[str] = []
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    profiles = {p["profile_id"] for p in bundle.environment_profiles["profiles"]}
    spe = {t["template_id"] for t in bundle.environment_profiles["spe_templates"]}
    thermal = {t["template_id"] for t in bundle.environment_profiles["thermal_failure_templates"]}
    lots = {lot["lot_id"]: lot for lot in bundle.lots["lots"]}
    products = {p["product_id"]: p for p in bundle.formulary["products"]}
    start = pt.iso_to_seconds(scenario["mission"]["start_utc"])
    end = pt.iso_to_seconds(scenario["mission"]["end_utc"])

    def inside(label: str, t_utc: str) -> None:
        if not start <= pt.iso_to_seconds(t_utc) <= end:
            problems.append(f"{label}: {t_utc} outside the mission")

    sensors_by_zone: dict[str, set[str]] = {}
    for zone in scenario["zones"]:
        if zone["zone_id"] not in zones:
            problems.append(f"zone {zone['zone_id']!r} not in storage-zones")
            continue
        if zone["profile_id"] not in profiles:
            problems.append(f"zone {zone['zone_id']!r}: unknown profile {zone['profile_id']!r}")
        configured = {s["sensor_id"]: s["quantity"] for s in zones[zone["zone_id"]]["sensors"]}
        sensors_by_zone[zone["zone_id"]] = set()
        for sensor in zone["sensors"]:
            sensors_by_zone[zone["zone_id"]].add(sensor["sensor_id"])
            if configured.get(sensor["sensor_id"]) != sensor["quantity"]:
                problems.append(
                    f"zone {zone['zone_id']!r}: sensor {sensor['sensor_id']!r} "
                    "not configured for that quantity"
                )
    for event in scenario["events"]:
        inside(f"event {event['event_id']}", event["start_utc"])
        if event["zone_id"] not in sensors_by_zone:
            problems.append(f"event {event['event_id']!r}: zone not simulated")
        templates = thermal if event["kind"] == "thermal_control_failure" else spe
        if (
            event["kind"] in {"thermal_control_failure", "solar_particle_event"}
            and event["template_id"] not in templates
        ):
            problems.append(f"event {event['event_id']!r}: unknown template {event['template_id']!r}")
        for sid in event["sensor_ids"]:
            if sid not in sensors_by_zone.get(event["zone_id"], set()):
                problems.append(f"event {event['event_id']!r}: sensor {sid!r} not in its zone")
        if event["kind"] == "sensor_outage" and (event["duration_s"] is None or not event["sensor_ids"]):
            problems.append(f"event {event['event_id']!r}: an outage needs a duration and sensors")
    for lot_id in scenario["lot_ids"]:
        if lot_id not in lots:
            problems.append(f"lot {lot_id!r} not in lots")
    for assay in scenario["assays"]:
        inside(f"assay {assay['assay_id']}", assay["measured_at_utc"])
        lot = lots.get(assay["lot_id"])
        if lot is None or assay["lot_id"] not in scenario["lot_ids"]:
            problems.append(f"assay {assay['assay_id']!r}: lot not in the scenario")
            continue
        ingredients = {i["ingredient_id"] for i in products[lot["product_id"]]["ingredients"]}
        if assay["ingredient_id"] not in ingredients:
            problems.append(f"assay {assay['assay_id']!r}: ingredient not in the product")
        if abs(pt.iso_to_seconds(assay["measured_at_utc"]) - assay["measured_at_s"]) > 1e-3:
            problems.append(f"assay {assay['assay_id']!r}: ISO and float times disagree")
    for insp in scenario["inspections"]:
        inside(f"inspection {insp['inspection_id']}", insp["inspected_at_utc"])
        if insp["lot_id"] not in scenario["lot_ids"]:
            problems.append(f"inspection {insp['inspection_id']!r}: lot not in the scenario")
        if abs(pt.iso_to_seconds(insp["inspected_at_utc"]) - insp["inspected_at_s"]) > 1e-3:
            problems.append(f"inspection {insp['inspection_id']!r}: ISO and float times disagree")
    for t_utc in scenario["assessment_times_utc"]:
        inside("assessment time", t_utc)
    for exp in scenario["expectations"]:
        inside(f"expectation {exp['expectation_id']}", exp["as_of_utc"])
        for lid in (exp["lot_id"], exp["other_lot_id"]):
            if lid is not None and lid not in scenario["lot_ids"]:
                problems.append(f"expectation {exp['expectation_id']!r}: lot {lid!r} not in the scenario")
        if exp["as_of_utc"] not in scenario["assessment_times_utc"]:
            problems.append(f"expectation {exp['expectation_id']!r}: as_of is not an assessment time")
    return problems


def repository_problems(repo_root: Path = REPO_ROOT) -> list[str]:
    """Everything ``scripts/validate_contracts.py`` checks (spec 2.4): schema drift, the
    configuration bundle (schemas and references), every fixture (schema and semantics) and
    every test-vector file (schema and unique ids). Empty when the repository is consistent."""
    problems = [f"schema drift: {name}" for name in drift(repo_root / "contracts" / "pharmacy")]
    try:
        bundle = load_bundle(repo_root / "config" / "pharmacy", repo_root / "contracts" / "pharmacy")
    except ConfigError as exc:
        return [*problems, f"bundle: {exc}"]
    for path in sorted((repo_root / "fixtures" / "pharmacy").glob("*.json")):
        try:
            doc = load_document(path, "pharmacy/scenario.schema.json")
        except ConfigError as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        problems += [f"{path.name}: {p}" for p in scenario_problems(cast(pt.ScenarioDoc, doc.data), bundle)]
    seen: dict[str, str] = {}
    vectors: dict[str, JSONObject] = {}
    for path in sorted((repo_root / "tests" / "data" / "vectors").glob("*.json")):
        data = load_json(path)
        errors = schema_violations(data, "pharmacy/test-vectors.schema.json")
        problems += [f"{path.name}{e}" for e in errors]
        if errors or not isinstance(data, dict) or not isinstance(data.get("vectors"), list):
            continue
        for vector in cast(list[dict[str, JSONValue]], data["vectors"]):
            vid = str(vector["vector_id"])
            if vid in seen:
                problems.append(f"{path.name}: vector {vid} duplicates {seen[vid]}")
            seen[vid] = path.name
            vectors[vid] = vector
    problems += calibration_reference_problems(bundle, vectors)
    return problems


def calibration_reference_problems(bundle: ConfigBundle, vectors: Mapping[str, JSONObject]) -> list[str]:
    """Resolve calibration dataset ids and row numbers against local vector data."""
    problems: list[str] = []

    def walk(value: JSONValue, pointer: str) -> None:
        if isinstance(value, dict):
            if "vector_id" in value and "rows" in value:
                vid = str(value["vector_id"])
                if vid not in vectors:
                    problems.append(f"{pointer}: unknown calibration vector {vid!r}")
                elif isinstance(value["rows"], list):
                    expected = vectors[vid].get("expected")
                    rows = expected.get("rows") if isinstance(expected, dict) else None
                    known: set[int] = set()
                    if isinstance(rows, list):
                        for row in rows:
                            parsed = row.get("parsed") if isinstance(row, dict) else None
                            number = parsed.get("row_number") if isinstance(parsed, dict) else None
                            if isinstance(number, int) and not isinstance(number, bool):
                                known.add(number)
                    for number in value["rows"]:
                        if not isinstance(number, int) or isinstance(number, bool) or number not in known:
                            problems.append(f"{pointer}: unknown calibration row {number!r} in {vid}")
            for key, child in value.items():
                walk(child, f"{pointer}/{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{pointer}/{index}")

    for member, document in sorted(bundle.documents.items()):
        walk(document.data, member)
    return problems


def main(argv: list[str]) -> int:
    """``python -m pharmacy.contracts [--write]``: check (default) or rewrite the schemas."""
    if "--write" in argv:
        for file_name, schema in generate_schemas().items():
            (DEFAULT_SCHEMA_DIR / file_name).write_text(render(schema), encoding="utf-8")
        return 0
    stale = drift()
    for file_name in stale:
        print(f"stale: {file_name}")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
