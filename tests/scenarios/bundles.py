# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Test bundles (B3): a copy of config/pharmacy in a scratch directory with documents added or
replaced, loaded and validated through ``load_bundle``. config/pharmacy is never written.

Used by tests/validation (V1 and V6 products built from their rows, declared station zone)
and tests/virtual (the VS-3 draft stability model and the virtual chamber products).

Stability models built by B3 tests go through ``stability_model``, which points the BDE
cache at a per-process copy of data/pharmacy/xtb-cache: a cache miss computed during a test
is written to the copy, never into B2's committed cache (request B3-R25).
"""

import copy
import functools
import json
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from pharmacy.config import ConfigBundle, load_bundle
from pharmacy.types import (
    JSONValue,
    LotDoc,
    ProductDoc,
    StabilityModel,
    StabilityModelDoc,
    StorageZoneDoc,
    iso_to_seconds,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config" / "pharmacy"
XTB_CACHE_DIR = REPO_ROOT / "data" / "pharmacy" / "xtb-cache"


@functools.cache
def isolated_xtb_cache() -> Path:
    """A per-process copy of the committed BDE cache (read once, never written back)."""
    target = Path(tempfile.mkdtemp(prefix="b3-xtb-cache-"))
    for path in sorted(XTB_CACHE_DIR.glob("*.json")):
        shutil.copyfile(path, target / path.name)
    return target


def stability_model(
    bundle: ConfigBundle, product_id: str, ingredient_id: str, package_ids: Sequence[str]
) -> StabilityModel:
    """``build_stability_model`` with the BDE cache at ``isolated_xtb_cache()``."""
    from pharmacy.chemistry.parameters import build_stability_model

    return build_stability_model(
        bundle, product_id, ingredient_id, list(package_ids), cache_dir=isolated_xtb_cache()
    )


def lot_package_ids(lot: LotDoc, as_of_s: float) -> list[str]:
    """The packages a lot has been in at or before ``as_of_s``, sorted."""
    return sorted(
        {p["package_id"] for p in lot["package_history"] if iso_to_seconds(p["from_utc"]) <= as_of_s}
    )


def _read(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")


def write_test_bundle(
    directory: Path,
    *,
    products: Sequence[ProductDoc] = (),
    lots: Sequence[LotDoc] | None = None,
    extra_lots: Sequence[LotDoc] = (),
    zones: Sequence[StorageZoneDoc] = (),
    zone_overrides: Sequence[StorageZoneDoc] = (),
    one_batch: bool = False,
    stability_models: Sequence[StabilityModelDoc] = (),
) -> ConfigBundle:
    """Copy config/pharmacy into ``directory`` (created), add ``products``, ``zones`` and
    ``stability_models``, replace the lots with ``lots`` when given, append ``extra_lots``,
    and load the result. Identifiers must not collide with configured ones."""
    directory.mkdir(parents=True, exist_ok=True)
    for path in sorted(CONFIG_DIR.glob("*.json")):
        shutil.copyfile(path, directory / path.name)
    if products:
        formulary = _read(directory / "formulary.json")
        formulary["products"] = [*formulary["products"], *(copy.deepcopy(p) for p in products)]
        _write(directory / "formulary.json", formulary)
    if lots is not None or extra_lots:
        lots_doc = _read(directory / "lots.json")
        base = list(lots_doc["lots"]) if lots is None else [copy.deepcopy(lot) for lot in lots]
        lots_doc["lots"] = [*base, *(copy.deepcopy(lot) for lot in extra_lots)]
        _write(directory / "lots.json", lots_doc)
    if zones or zone_overrides:
        zones_doc = _read(directory / "storage-zones.json")
        overrides = {z["zone_id"]: z for z in zone_overrides}
        existing = {z["zone_id"] for z in zones_doc["zones"]}
        if set(overrides) - existing:
            raise KeyError(sorted(set(overrides) - existing))
        zones_doc["zones"] = [
            *(copy.deepcopy(overrides.get(z["zone_id"], z)) for z in zones_doc["zones"]),
            *(copy.deepcopy(z) for z in zones),
        ]
        _write(directory / "storage-zones.json", zones_doc)
    if stability_models:
        models_doc = _read(directory / "stability-models.json")
        models_doc["models"] = [*models_doc["models"], *(copy.deepcopy(m) for m in stability_models)]
        _write(directory / "stability-models.json", models_doc)
    if one_batch:
        settings = _read(directory / "engine-settings.json")
        settings["monte_carlo"]["min_batches"]["value"] = 1
        settings["monte_carlo"]["max_batches"]["value"] = 1
        _write(directory / "engine-settings.json", settings)
    return load_bundle(directory)


def configured_zone(zone_id: str) -> StorageZoneDoc:
    for zone in load_bundle().zones["zones"]:
        if zone["zone_id"] == zone_id:
            return copy.deepcopy(zone)
    raise KeyError(zone_id)


def configured_product(product_id: str) -> ProductDoc:
    for product in load_bundle().formulary["products"]:
        if product["product_id"] == product_id:
            return copy.deepcopy(product)
    raise KeyError(product_id)


def configured_lot(lot_id: str) -> LotDoc:
    for lot in load_bundle().lots["lots"]:
        if lot["lot_id"] == lot_id:
            return copy.deepcopy(lot)
    raise KeyError(lot_id)


def replace_zone(bundle: ConfigBundle, zone: StorageZoneDoc) -> ConfigBundle:
    """Write and reload a scratch bundle, preserving valid hashes after a zone change."""
    directory = Path(tempfile.mkdtemp(prefix="cxd-validation-zone-"))
    found = False
    for document in bundle.documents.values():
        data = copy.deepcopy(document.data)
        if document.document_id == "storage-zones":
            rows = data["zones"]
            assert isinstance(rows, list)
            for i, row in enumerate(rows):
                assert isinstance(row, dict)
                if row["zone_id"] == zone["zone_id"]:
                    rows[i] = cast(JSONValue, copy.deepcopy(zone))
                    found = True
        _write(directory / Path(document.path).name, data)
    if not found:
        raise KeyError(zone["zone_id"])
    return load_bundle(directory)
