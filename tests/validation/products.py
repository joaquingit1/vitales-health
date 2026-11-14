# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Products, lots and zones of the validation datasets, built in tests (spec 15.5), never in
config/pharmacy.

Each V1 and V6 row becomes one product with one ingredient: the API structure, CID and
molecular weight come from tests/validation/pubchem-apis.json (looked up once by name from
PubChem, MDX01, with the row number), the dose-form family from the row's dose form, the
specification from the row's USP range (V1) or 90 to 110 (V6, not given). Lots start at
the first measurement with that measurement as the initial assay (V1: day-13 control mean
and sd; V6: the lots.json team default 100 +- 1.5) and stay in one declared zone in one
package: ``repackaged_bag`` when the row says repackaged, else ``original_bottle`` for solids
and ``original_vial`` for liquids and semisolids.
"""

import copy
import dataclasses
import datetime
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal, cast

from pharmacy.config import ConfigBundle
from pharmacy.errors import ConfigReferenceError
from pharmacy.types import DoseFormFamily, LotDoc, ProductDoc, StorageZoneDoc, TotalRatePrior
from tests.scenarios.bundles import configured_product, configured_zone, replace_zone

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parents[0] / "data" / "vectors"
PUBCHEM_PATH = HERE / "pubchem-apis.json"

DU_LAUNCH_UTC: Final[datetime.datetime] = datetime.datetime(2006, 7, 4, tzinfo=datetime.UTC)
"""Du 2011 kits flew to the ISS from 4 July 2006 (V1 conditions); day d is launch + d days."""
V6_START_UTC: Final[datetime.datetime] = datetime.datetime(2010, 1, 1, tzinfo=datetime.UTC)
"""V6 gives storage days only; the absolute start does not enter the chemistry."""
V1_EXCLUDED_ROWS: Final[frozenset[int]] = frozenset({22, 30})
"""Solution rows used in the solution prior (spec 15.5), excluded from V1 validation."""
V6_EXCLUDED_PRODUCTS: Final[frozenset[str]] = frozenset({"Aspirin"})
"""Calibration anchor of the computed layer (spec 15.5)."""
DECLARED_STATION_ZONE_ID: Final[str] = "station_kit"
V1_FLIGHT_ZONE_ID: Final[str] = "station_kit"
TEAM_DEFAULT_ASSAY = (100.0, 1.5)

Arm = Literal["control", "flight"]


@dataclasses.dataclass(frozen=True, slots=True)
class ValidationCase:
    """One product of one validation row in one arm, with what the engine must predict."""

    dataset: str
    row_number: int
    label: str
    arm: Arm
    product: ProductDoc
    lot: LotDoc
    ingredient_id: str
    measurement_days: tuple[int, ...]
    measured_percent: tuple[float | None, ...]
    measured_sd_percent: tuple[float | None, ...]
    start_utc: str
    excluded_reason: str | None


def iso(t: datetime.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_dataset(file_name: str, vector_id: str) -> dict[str, Any]:
    data = json.loads((DATA_DIR / file_name).read_text(encoding="utf-8"))
    for vector in data["vectors"]:
        if vector["vector_id"] == vector_id:
            result: dict[str, Any] = vector
            return result
    raise KeyError(vector_id)


def pubchem_entries() -> dict[tuple[str, int], dict[str, Any]]:
    data = json.loads(PUBCHEM_PATH.read_text(encoding="utf-8"))
    return {(e["dataset"], e["row_number"]): e for e in data["entries"]}


def dose_form_of(label: str) -> tuple[str, DoseFormFamily]:
    """The dose form named in the row label and its family (spec 15.5: from the row)."""
    text = label.lower()
    for word, form, family in (
        ("ophthalmic solution", "ophthalmic solution", "solution"),
        ("injection", "injection solution", "solution"),
        ("cream", "cream", "semisolid"),
        ("ointment", "ointment", "semisolid"),
        ("suppository", "suppository", "semisolid"),
        ("powder", "powder for injection", "solid"),
        ("capsule", "capsule", "solid"),
        ("tablet", "tablet", "solid"),
        ("oral contraceptive", "tablet", "solid"),
    ):
        if word in text:
            return form, cast(DoseFormFamily, family)
    return "tablet", "solid"


def package_for(repackaged: bool | None, family: DoseFormFamily) -> str:
    if repackaged:
        return "repackaged_bag"
    return "original_bottle" if family == "solid" else "original_vial"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _sourced(value: float, unit: str, note: str, source_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "basis": "sourced",
        "source_ids": list(source_ids),
        "to_be_set_by": None,
        "note": note,
    }


def build_product(
    product_id: str,
    name: str,
    label: str,
    family: DoseFormFamily,
    dose_form: str,
    api: Mapping[str, Any],
    strength_mg: float,
    spec_low: float,
    spec_high: float,
    spec_sources: Sequence[str],
    default_package: str,
) -> ProductDoc:
    """A formulary product with one ingredient from a validation row. The template is a
    configured product of the same family (solid: acetaminophen tablet; liquids and
    semisolids: promethazine injection) with every identifying field replaced."""
    template_id = "acetaminophen_325_tab" if family == "solid" else "promethazine_25mg_ml_inj"
    product = cast(dict[str, Any], configured_product(template_id))
    ingredient_id = _slug(str(api["query_name"]))
    ingredient = copy.deepcopy(product["ingredients"][0])
    ingredient.update(
        {
            "ingredient_id": ingredient_id,
            "name": str(api["query_name"]).capitalize(),
            "smiles": api["smiles"],
            "pubchem_cid": api["pubchem_cid"],
            "molar_mass_g_per_mol": _sourced(
                api["molecular_weight_g_per_mol"], "g/mol", "PubChem molecular weight (free form).", ["MDX01"]
            ),
            "salt_form_note": api.get("note", ""),
            "strength": {"value": strength_mg, "unit": "mg", "per": "unit"},
            "concentration_mol_per_kg": None,
            "specification": {
                "lower_percent": _sourced(spec_low, "percent", f"Row {label}: USP range.", spec_sources),
                "upper_percent": _sourced(spec_high, "percent", f"Row {label}: USP range.", spec_sources),
            },
            "degradants": [],
            "racemization": None,
            "source_ids": ["MDX01"],
        }
    )
    product.update(
        {
            "product_id": product_id,
            "name": name,
            "dose_form": dose_form,
            "dose_form_family": family,
            "route": "oral" if family == "solid" else product["route"],
            "ingredients": [ingredient],
            "default_package_id": default_package,
            "radiosensitive": False,
            "degradant_profile_reviewed": False,
            "label_shelf_life_months_text": "not used",
            "basis": "derived",
            "source_ids": list(spec_sources),
            "note": f"B3 validation product built from {label} (tests/validation, spec 15.5).",
        }
    )
    if family == "solid":
        product["freezing_point_k"] = None
        product["do_not_freeze"] = False
    return cast(ProductDoc, product)


def build_lot(
    lot_id: str,
    product_id: str,
    ingredient_id: str,
    zone_id: str,
    package_id: str,
    start: datetime.datetime,
    end: datetime.datetime,
    assay_mean: float,
    assay_sd: float,
    note: str,
) -> LotDoc:
    t0 = iso(start)
    return cast(
        LotDoc,
        {
            "lot_id": lot_id,
            "product_id": product_id,
            "manufacturer_lot": lot_id.upper().replace("-", ""),
            "manufactured_at_utc": t0,
            "labeled_expiry_utc": iso(end + datetime.timedelta(days=365)),
            "quantity": {"value": 1, "unit": "units"},
            "initial_assay": [
                {
                    "ingredient_id": ingredient_id,
                    "mean_percent": _sourced(assay_mean, "percent", note, ["MDP02"]),
                    "sd_percent": _sourced(assay_sd, "percent", note, ["MDP02"]),
                }
            ],
            "custody": [{"zone_id": zone_id, "from_utc": t0, "to_utc": None}],
            "package_history": [{"package_id": package_id, "from_utc": t0}],
            "status": "active",
            "basis": "derived",
            "source_ids": ["MDP02"],
            "note": note,
        },
    )


def declared_station_zone(bundle: ConfigBundle | None = None) -> StorageZoneDoc:
    """A declared copy of station_kit at the station profile setpoints (V1 flight arm, V6).

    Means are the profile setpoints; the sd reads the profile's nominal range as +-2 sd
    (the convention of ground_storage); the dose rate is the profile's absorbed dose rate;
    the rate multiplier, worst case and light exposure are station_kit's."""
    from pharmacy.config import load_bundle

    bundle = load_bundle() if bundle is None else bundle
    station = copy.deepcopy(next(z for z in bundle.zones["zones"] if z["zone_id"] == "station_kit"))
    profile = next(p for p in bundle.environment_profiles["profiles"] if p["profile_id"] == "station")
    zone = cast(
        dict[str, Any],
        copy.deepcopy(next(z for z in bundle.zones["zones"] if z["zone_id"] == "ground_storage")),
    )
    t, rh = profile["temperature"], profile["relative_humidity"]
    note = "B3 validation: declared copy of station_kit at the station profile setpoints (spec 15.5)."

    def normal(mu: float, low: float, high: float, unit: str) -> dict[str, Any]:
        return {
            "family": "normal",
            "mu": mu,
            "sigma": (high - low) / 4,
            "lower": None,
            "upper": None,
            "unit": unit,
            "basis": "derived",
            "source_ids": list(t["setpoint_k"]["source_ids"]),
            "to_be_set_by": None,
            "note": note,
        }

    declared = copy.deepcopy(zone["declared_conditions"])
    declared["temperature_k"] = normal(
        t["setpoint_k"]["value"], t["nominal_min_k"]["value"], t["nominal_max_k"]["value"], "K"
    )
    declared["relative_humidity_percent"] = normal(
        rh["setpoint_percent"]["value"],
        rh["nominal_min_percent"]["value"],
        rh["nominal_max_percent"]["value"],
        "percent",
    )
    declared["dose_rate_gy_per_s"].update(
        {
            "mu": profile["radiation"]["absorbed_dose_rate_gy_per_day"]["value"] / 86400.0,
            "note": note,
            "source_ids": list(profile["radiation"]["absorbed_dose_rate_gy_per_day"]["source_ids"]),
        }
    )
    zone.update(
        {
            "zone_id": DECLARED_STATION_ZONE_ID,
            "name": "Station medical kit (declared, B3 validation)",
            "declared_conditions": declared,
            "rate_multiplier": copy.deepcopy(station["rate_multiplier"]),
            "rate_multiplier_calibration": copy.deepcopy(station.get("rate_multiplier_calibration")),
            "worst_case": copy.deepcopy(station["worst_case"]),
            "light_exposure": station["light_exposure"],
            "note": note,
        }
    )
    return cast(StorageZoneDoc, zone)


def _condition_zone(
    zone_id: str,
    temperature: float,
    temperature_sd: float,
    rh: float,
    rh_sd: float,
    template: StorageZoneDoc,
) -> StorageZoneDoc:
    zone = copy.deepcopy(template)
    zone["zone_id"] = zone_id
    zone["name"] = zone_id
    declared = zone["declared_conditions"]
    assert declared is not None
    for key, mu, sd in (
        ("temperature_k", temperature, temperature_sd),
        ("relative_humidity_percent", rh, rh_sd),
    ):
        prior = cast(dict[str, Any], declared)[key]
        assert prior is not None
        prior.update(
            mu=mu,
            sigma=sd,
            family="normal",
            basis="derived",
            note="Spec 15.5: condition read from calibration metadata at test time.",
        )
    return zone


def ground_zone(prior: TotalRatePrior) -> StorageZoneDoc:
    """V1 ground condition from the matching total-rate prior, never warehouse setpoints."""
    return _condition_zone(
        f"b3_v1_ground_{prior['dose_form_family']}",
        prior["temperature_k"]["value"],
        prior["temperature_sd_k"]["value"],
        prior["rh_percent"]["value"],
        prior["rh_sd_percent"]["value"],
        configured_zone("ground_storage"),
    )


def flight_zone(bundle: ConfigBundle) -> StorageZoneDoc:
    """V1 flight condition from the station multiplier's calibration provenance.

    Missing calibration metadata raises ConfigReferenceError; callers skip flight
    validation with this reason until CX-CAL supplies the required statistics.
    """
    station = next(z for z in bundle.zones["zones"] if z["zone_id"] == "station_kit")
    provenance = station.get("rate_multiplier_calibration")
    names = ("flight_temperature_k", "flight_temperature_sd_k", "flight_rh_percent", "flight_rh_sd_percent")
    stats = {} if provenance is None else {s["name"]: s["value"] for s in provenance["statistics"]}
    if any(n not in stats for n in names):
        raise ConfigReferenceError(
            "V1 flight", ["awaits CX-CAL station_kit calibration condition statistics"]
        )
    template = declared_station_zone(bundle)
    template["rate_multiplier"] = copy.deepcopy(station["rate_multiplier"])
    return _condition_zone(
        V1_FLIGHT_ZONE_ID, stats[names[0]], stats[names[1]], stats[names[2]], stats[names[3]], template
    )


def validation_zones(bundle: ConfigBundle) -> list[StorageZoneDoc]:
    """V1 ground zones derived from each calibration prior."""
    return [ground_zone(p) for p in bundle.reaction_classes["total_rate_priors"]]


def with_flight_condition(bundle: ConfigBundle) -> ConfigBundle:
    """Replace station conditions in a private bundle, preserving the shared zone ID.

    A new zone ID would incorrectly make the station anchor correction independent of
    the station lot's multiplier draw (spec 9.3 and 9.7).
    """
    return replace_zone(bundle, flight_zone(bundle))


V1_DAYS: Final[tuple[int, ...]] = (13, 353, 596, 880)


def v1_cases(arm: Arm, *, all_rows: bool = False) -> list[ValidationCase]:
    """V1 (and its flight arm): the even rows with day-13 and day-880 control values, rows 22
    and 30 marked excluded; ``all_rows`` adds the odd (calibration) rows, marked excluded,
    for the V2 fleet count (report only)."""
    v1 = load_dataset("validation-du2011.json", "V1")
    apis = pubchem_entries()

    start = DU_LAUNCH_UTC + datetime.timedelta(days=13)
    end = DU_LAUNCH_UTC + datetime.timedelta(days=880)
    out = []
    for row in v1["expected"]["rows"]:
        p = row["parsed"]
        n = p["row_number"]
        if p["day_13_control"] is None or p["day_880_control"] is None:
            continue
        excluded = None
        if n % 2 == 1:
            excluded = "calibration row (odd row number)"
            if not all_rows:
                continue
        elif n in V1_EXCLUDED_ROWS:
            excluded = "solution row used in the solution prior (spec 15.5)"
        form, family = dose_form_of(p["product_api"])
        zone_id = f"b3_v1_ground_{family}" if arm == "control" else V1_FLIGHT_ZONE_ID
        package = package_for(p["repackaged"], family)
        api = apis[("V1", n)]
        product_id = f"b3_v1_row_{n:02d}"
        usp = p["usp_range_percent"]
        product = build_product(
            product_id,
            p["product_api"],
            f"V1 row {n}",
            family,
            form,
            api,
            float(p["label_strength_mg"]),
            float(usp["low"]),
            float(usp["high"]),
            ["MDP02", "MDP03"],
            package,
        )
        ingredient_id = product["ingredients"][0]["ingredient_id"]
        initial = p["day_13_control"]
        lot = build_lot(
            f"lot-b3-v1-{arm}-{n:02d}",
            product_id,
            ingredient_id,
            zone_id,
            package,
            start,
            end,
            initial["value"],
            initial["sd"],
            f"V1 row {n}: day-13 control mean and sd as initial assay.",
        )
        measured = tuple(
            None if p[f"day_{d}_{arm}"] is None else p[f"day_{d}_{arm}"]["value"] for d in V1_DAYS
        )
        sds = tuple(None if p[f"day_{d}_{arm}"] is None else p[f"day_{d}_{arm}"]["sd"] for d in V1_DAYS)
        out.append(
            ValidationCase(
                "V1" if arm == "control" else "V1-flight",
                n,
                p["product_api"],
                arm,
                product,
                lot,
                ingredient_id,
                V1_DAYS,
                measured,
                sds,
                iso(start),
                excluded,
            )
        )
    return out


def v6_cases() -> list[ValidationCase]:
    v6 = load_dataset("validation-iss-short.json", "V6")
    apis = pubchem_entries()
    days = int(v6["inputs"]["storage_days"])
    start = V6_START_UTC
    end = start + datetime.timedelta(days=days)
    out = []
    for index, row in enumerate(v6["expected"]["rows"], start=1):
        p = row["parsed"]
        excluded = (
            "calibration anchor of the computed layer (spec 15.5)"
            if p["product"] in V6_EXCLUDED_PRODUCTS
            else None
        )
        if excluded:
            continue
        api = apis[("V6", index)]
        family: DoseFormFamily = "solid"
        package = "repackaged_bag"
        product_id = f"b3_v6_row_{index:02d}"
        product = build_product(
            product_id,
            p["product"],
            f"V6 row {index}",
            family,
            "tablet",
            api,
            float(p["label"]["value"]),
            90.0,
            110.0,
            ["MDP05"],
            package,
        )
        ingredient_id = product["ingredients"][0]["ingredient_id"]
        lot = build_lot(
            f"lot-b3-v6-{index:02d}",
            product_id,
            ingredient_id,
            DECLARED_STATION_ZONE_ID,
            package,
            start,
            end,
            TEAM_DEFAULT_ASSAY[0],
            TEAM_DEFAULT_ASSAY[1],
            "V6: lots.json team default initial assay 100 +- 1.5.",
        )
        sd_percent = 100.0 * p["measured_mean_sd_sd_mg"]["sd"] / p["label"]["value"]
        out.append(
            ValidationCase(
                "V6",
                index,
                p["product"],
                "flight",
                product,
                lot,
                ingredient_id,
                (days,),
                (float(p["percent_lc"]),),
                (sd_percent,),
                iso(start),
                None,
            )
        )
    return out
