"""E32-T-59, static: `rule.no_dose` (CONTRACTS EI6: "Validator `rule.no_dose` over every schema, document and plan
step", M-L7: it holds everywhere except treatments.json) and no assistant tool reaching a medicines operation (E32
10.4: "No tool maps to an E32 operation ... against tool-catalog.json"). The hub has no rule.no_dose validator yet
and no config/tool-catalog.json, so this scan runs over the runtime documents in config/ and the catalog's
specification in docs/product/."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from .harness import CONFIG, REPO

CATALOG = REPO / "docs/product/tool-catalog.json"
OPENAPI = REPO / "contracts/api/openapi.yaml"
TREATMENTS = CONFIG / "treatments.json"
PHARMACY = CONFIG / "pharmacy"
DOSE_KEYS = frozenset(
    {
        "dose",
        "dose_per_kg",
        "max_per_administration",
        "max_per_period",
        "max_cumulative",
        "min_effective",
        "catastrophic_limits",
    }
)
# The potency engine's own bundle (config/pharmacy) names "dose" in three senses, none of them an amount to give a
# person. Each is allowed only at the exact place it stands today, so a dose field added anywhere in the bundle fails:
# 1. the dosage form of a product (tablet, solid), never an amount;
# 2. ionizing radiation absorbed by the stored medicine (Gy, Sv), an input to its degradation;
# 3. ICH Q3B(R2) degradation product thresholds, banded by maximum daily dose (engine-settings q3b): the band edge
#    picks a reporting threshold for impurities, it doses nobody.
PHARMACY_ALLOWED = frozenset(
    {
        ("formulary.json", "/products/*/dose_form"),
        ("formulary.json", "/products/*/dose_form_family"),
        ("formulary.json", "/products/*/fixed_dose_device"),
        ("engine-settings.json", "/rate_law_defaults/*/dose_form_family"),
        ("radiation-chemistry.json", "/upper_bounds/*/dose_form_family"),
        ("reaction-classes.json", "/total_rate_priors/*/dose_form_family"),
        ("reaction-classes.json", "/classes/*/anchors/*/dose_form_family"),
        ("reaction-classes.json", "/classes/*/priors/*/dose_form_family"),
        ("engine-settings.json", "/radiation/dose_rate_safety_factor"),
        ("engine-settings.json", "/radiation/radiosensitive_dose_threshold_gy"),
        ("environment-profiles.json", "/profiles/*/radiation/absorbed_dose_rate_gy_per_day"),
        ("environment-profiles.json", "/profiles/*/radiation/absorbed_dose_rate_sd_gy_per_day"),
        ("environment-profiles.json", "/profiles/*/radiation/dose_equivalent_rate_sv_per_day"),
        ("environment-profiles.json", "/profiles/*/sensor_defaults/dose_rate_noise_relative_sd"),
        ("environment-profiles.json", "/profiles/*/sensor_defaults/dose_rate_resolution_gy_per_s"),
        ("environment-profiles.json", "/spe_templates/*/total_absorbed_dose_gy"),
        ("storage-zones.json", "/zones/*/declared_conditions/dose_rate_gy_per_s"),
        ("storage-zones.json", "/zones/*/gap_policy/bounds/dose_rate_gy_per_s"),
        ("storage-zones.json", "/zones/*/worst_case/dose_rate_max_gy_per_s"),
        ("engine-settings.json", "/q3b/reporting/*/dose_upper_mg"),
        ("engine-settings.json", "/q3b/identification/*/dose_upper_mg"),
        ("engine-settings.json", "/q3b/qualification/*/dose_upper_mg"),
    }
)
# The medicines screen's labels for the engine's radiation input and flag (sense 2 above), never an amount to give.
UI_COPY_ALLOWED = frozenset({"/medicine_words/dose", "/medicine_words/flag_radiosensitive_dose_exceeded"})
MEDICINE_PATHS = (
    "/v1/medicines",
    "/v1/dose-proposals",
    "/v1/medication-administrations",
    "/v1/people/{person_id}/medication-context",
    "/v1/episodes/{episode_id}/working-diagnoses",
    "/v1/working-diagnoses/",
)
NAMED_IN_E32 = frozenset(
    {
        "listMedicineLots",
        "recordMedicineInspection",
        "recordMedicineAssay",
        "scheduleMedicineAssessment",
        "recordMedicationContext",
        "getMedicationContext",
        "recordWorkingDiagnosis",
        "listTreatmentOptions",
        "createDoseProposal",
        "getDoseProposal",
        "confirmDoseProposal",
        "recordManualAdministration",
        "listMedicationAdministrations",
        "correctMedicationAdministration",
        "recordLotMovement",
        "exportMedicationAdministration",
    }
)


def _keys(value: object, path: str = "") -> Iterator[str]:
    """Every object key as a JSON pointer, list positions written `*`."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield f"{path}/{key}"
            yield from _keys(child, f"{path}/{key}")
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child, f"{path}/*")


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _is_dose_field(pointer: str) -> bool:
    key = pointer.rsplit("/", 1)[-1]
    return key in DOSE_KEYS or "dose" in key.lower()


def _scanned() -> list[Path]:
    return sorted(CONFIG.rglob("*.json")) + [CATALOG]


@pytest.mark.trace("T-59", "INV-M6", "EI6", "HZ10")
def test_no_dose_field_outside_treatments() -> None:
    offenders = []
    for path in _scanned():
        if path == TREATMENTS:
            continue
        for pointer in _keys(json.loads(path.read_text(encoding="utf-8"))):
            allowed = (path.parent == PHARMACY and (path.name, pointer) in PHARMACY_ALLOWED) or (
                path == CONFIG / "ui-copy.json" and pointer in UI_COPY_ALLOWED
            )
            if _is_dose_field(pointer) and not allowed:
                offenders.append(f"{path.relative_to(REPO)}:{pointer}")
    assert offenders == []


@pytest.mark.trace("T-59", "EI6")
def test_the_scan_sees_the_dose_fields_where_they_belong() -> None:
    """Not vacuous: the scanned set includes the catalog and the pharmacy bundle, treatments.json carries every dose
    field name, and every pharmacy allowance still matches a field (a stale allowance would hide nothing)."""
    scanned = _scanned()
    assert CATALOG in scanned and TREATMENTS in scanned and (PHARMACY / "formulary.json") in scanned
    treatment_keys = {p.rsplit("/", 1)[-1] for p in _keys(json.loads(TREATMENTS.read_text(encoding="utf-8")))}
    assert DOSE_KEYS <= treatment_keys
    present = {
        (path.name, pointer)
        for path in sorted(PHARMACY.glob("*.json"))
        for pointer in _keys(json.loads(path.read_text(encoding="utf-8")))
    }
    assert PHARMACY_ALLOWED <= present


def _medicine_operations() -> frozenset[str]:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    found = set()
    for path, item in spec["paths"].items():
        if str(path).startswith(MEDICINE_PATHS):
            found |= {
                str(op["operationId"]) for op in item.values() if isinstance(op, dict) and "operationId" in op
            }
    return frozenset(found)


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word.capitalize() for word in rest)


@pytest.mark.trace("T-59", "AGENTS-4", "10.4")
def test_no_assistant_tool_names_a_medicines_operation_or_path() -> None:
    operations = _medicine_operations()
    assert NAMED_IN_E32 <= operations
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    tools = catalog["tools"]
    assert tools
    paths = ["[^/]+".join(re.escape(part) for part in re.split(r"\{[^}]+\}", p)) for p in MEDICINE_PATHS]
    pattern = re.compile("|".join(paths + [re.escape(op) for op in sorted(operations)]), re.IGNORECASE)
    assert pattern.search("POST /v1/people/person_a/medication-context") and not pattern.search(
        "/v1/people/x"
    )
    offenders = []
    for tool in tools:
        name = str(tool["name"])
        if _camel(name) in operations:
            offenders.append(f"{name}: named as {_camel(name)}")
        offenders += [f"{name}: {text}" for text in _strings(tool) if pattern.search(text)]
    assert offenders == []
