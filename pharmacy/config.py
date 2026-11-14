"""Configuration loading, schema validation, canonical hashing and reference checks.

Implemented (foundation). See docs/ENGINE-SPEC.md sections 1.4 (canonical JSON and pins)
and 2.3 (configuration documents).

Every configuration document is a JSON object whose ``schema`` member names its JSON
Schema in ``contracts/pharmacy`` (for example ``pharmacy/formulary.schema.json``). Loading
a document parses it strictly (no NaN or Infinity, no duplicate keys), validates it with
JSON Schema 2020-12 and computes the SHA-256 of its canonical JSON form. A bundle loads the
eleven documents of the engine and checks every cross-document reference.
"""

import dataclasses
import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from pharmacy.errors import ConfigLoadError, ConfigReferenceError, ConfigSchemaError
from pharmacy.types import (
    ConfigPin,
    EngineSettingsDoc,
    EnvironmentProfilesDoc,
    FormularyDoc,
    JSONObject,
    JSONValue,
    LferConstantsDoc,
    LotsDoc,
    PackagesDoc,
    RadiationChemistryDoc,
    ReactionClassesDoc,
    SourcesDoc,
    StabilityModelsDoc,
    StorageZonesDoc,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA_DIR = REPO_ROOT / "contracts" / "pharmacy"
DEFAULT_CONFIG_DIR = REPO_ROOT / "config" / "pharmacy"
SCHEMA_PREFIX = "pharmacy/"
SCHEMA_BASE_URI = "https://crew-health-check.invalid/schemas/"
"""Base of every schema ``$id``. A document's ``schema`` member is the part after the base
(``pharmacy/<name>.schema.json``). The ``.invalid`` top-level domain (RFC 2606) is never
resolvable, so validation always uses the local registry and never the network."""

BUNDLE_FILES: Mapping[str, str] = {
    "sources": "sources.json",
    "engine-settings": "engine-settings.json",
    "reaction-classes": "reaction-classes.json",
    "lfer-constants": "lfer-constants.json",
    "radiation-chemistry": "radiation-chemistry.json",
    "packages": "packages.json",
    "storage-zones": "storage-zones.json",
    "formulary": "formulary.json",
    "stability-models": "stability-models.json",
    "environment-profiles": "environment-profiles.json",
    "lots": "lots.json",
}
"""Bundle member name to file name. The schema of member ``x`` is ``pharmacy/x.schema.json``."""


# =========================================================================================
# Canonical JSON and hashing
# =========================================================================================


def canonical_json_bytes(value: JSONValue) -> bytes:
    """Canonical serialization used for every hash and seed in the engine (spec 1.4):
    ``json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    allow_nan=False)`` encoded as UTF-8. Numbers keep their parsed Python type, so ``1`` and
    ``1.0`` serialize differently; floats use Python's shortest round-trip repr."""
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except ValueError as exc:  # NaN or infinity
        raise ConfigLoadError(f"value cannot be serialized canonically: {exc}", "<memory>") from exc
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Lower-case hexadecimal SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: JSONValue) -> str:
    """SHA-256 of the canonical JSON of ``value``."""
    return sha256_hex(canonical_json_bytes(value))


# =========================================================================================
# Strict JSON loading
# =========================================================================================


def _reject_constant(name: str) -> float:
    raise ValueError(f"non-standard JSON constant {name}")


def _no_duplicates(pairs: list[tuple[str, JSONValue]]) -> dict[str, JSONValue]:
    out: dict[str, JSONValue] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def parse_json_text(text: str, origin: str) -> JSONValue:
    """Parse JSON text strictly: NaN, Infinity and duplicate object keys are errors."""
    try:
        return cast(
            JSONValue,
            json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_no_duplicates),
        )
    except ValueError as exc:
        raise ConfigLoadError(f"invalid JSON: {exc}", origin) from exc


def load_json(path: Path) -> JSONValue:
    """Read and strictly parse a UTF-8 JSON file. Raises ``ConfigLoadError``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigLoadError(f"cannot read file: {exc.strerror}", str(path)) from exc
    return parse_json_text(text, str(path))


# =========================================================================================
# Schema registry and validation
# =========================================================================================


@cache
def schema_registry(schema_dir: Path = DEFAULT_SCHEMA_DIR) -> Registry[Any]:
    """Registry of every ``*.schema.json`` in ``schema_dir`` keyed by its ``$id``
    (``SCHEMA_BASE_URI`` + ``pharmacy/<name>.schema.json``), so relative ``$ref`` between
    schemas resolve offline."""
    resources: list[tuple[str, Resource[Any]]] = []
    for path in sorted(schema_dir.glob("*.schema.json")):
        contents = load_json(path)
        if not isinstance(contents, dict) or not isinstance(contents.get("$id"), str):
            raise ConfigLoadError("schema without a string $id", str(path))
        resources.append(
            (str(contents["$id"]), Resource.from_contents(contents, default_specification=DRAFT202012))
        )
    return Registry[Any]().with_resources(resources)


def schema_uri(schema_id: str) -> str:
    """Full ``$id`` of the schema a document names in its ``schema`` member."""
    return SCHEMA_BASE_URI + schema_id


def schema_ids(schema_dir: Path = DEFAULT_SCHEMA_DIR) -> tuple[str, ...]:
    """Sorted short ids (``pharmacy/<name>.schema.json``) of every schema in ``schema_dir``."""
    return tuple(sorted(str(uri).removeprefix(SCHEMA_BASE_URI) for uri in schema_registry(schema_dir)))


@cache
def _validator(schema_id: str, schema_dir: Path) -> Draft202012Validator:
    registry = schema_registry(schema_dir)
    try:
        schema = registry.contents(schema_uri(schema_id))
    except LookupError as exc:
        raise ConfigLoadError(f"unknown schema {schema_id!r}", str(schema_dir)) from exc
    Draft202012Validator.check_schema(cast(Mapping[str, object], schema))
    return Draft202012Validator(cast(Mapping[str, object], schema), registry=registry)


def schema_violations(
    instance: JSONValue, schema_id: str, schema_dir: Path = DEFAULT_SCHEMA_DIR
) -> list[str]:
    """All violations of ``instance`` against ``schema_id`` as ``/json/pointer: message``
    strings, sorted for stable output. Empty when valid."""
    validator = _validator(schema_id, schema_dir)
    messages = []
    for error in validator.iter_errors(instance):
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        messages.append(f"{pointer}: {error.message}")
    return sorted(messages)


def validate_instance(
    instance: JSONValue, schema_id: str, label: str, schema_dir: Path = DEFAULT_SCHEMA_DIR
) -> None:
    """Raise ``ConfigSchemaError`` (labelled ``label``) if ``instance`` violates the schema."""
    errors = schema_violations(instance, schema_id, schema_dir)
    if errors:
        raise ConfigSchemaError(label, errors)


# =========================================================================================
# Documents and bundles
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class ConfigDocument:
    """A validated configuration document with its canonical hash."""

    document_id: str
    schema_id: str
    version: str
    path: str
    sha256: str
    data: JSONObject

    def pin(self) -> ConfigPin:
        return ConfigPin(document_id=self.document_id, version=self.version, sha256=self.sha256)


def document_from_json(
    data: JSONValue, origin: str, expected_schema: str | None = None, schema_dir: Path = DEFAULT_SCHEMA_DIR
) -> ConfigDocument:
    """Validate an already parsed document. The schema is the document's own ``schema``
    member; ``expected_schema`` (when given) must equal it."""
    if not isinstance(data, dict):
        raise ConfigLoadError("a configuration document must be a JSON object", origin)
    schema_id = data.get("schema")
    if not isinstance(schema_id, str) or not schema_id.startswith(SCHEMA_PREFIX):
        raise ConfigLoadError("missing or invalid 'schema' member", origin)
    if expected_schema is not None and schema_id != expected_schema:
        raise ConfigLoadError(f"expected schema {expected_schema!r}, document declares {schema_id!r}", origin)
    label = str(data.get("document_id", origin))
    validate_instance(data, schema_id, label, schema_dir)
    return ConfigDocument(
        document_id=str(data["document_id"]),
        schema_id=schema_id,
        version=str(data["version"]),
        path=origin,
        sha256=canonical_sha256(data),
        data=data,
    )


def load_document(
    path: Path, expected_schema: str | None = None, schema_dir: Path = DEFAULT_SCHEMA_DIR
) -> ConfigDocument:
    """Load, validate and hash one document. Raises ``ConfigLoadError`` or
    ``ConfigSchemaError``."""
    return document_from_json(load_json(path), str(path), expected_schema, schema_dir)


@dataclasses.dataclass(frozen=True, slots=True)
class ConfigBundle:
    """The eleven validated documents the engine runs on, with typed views."""

    documents: Mapping[str, ConfigDocument]

    def _data(self, member: str) -> JSONObject:
        return self.documents[member].data

    @property
    def sources(self) -> SourcesDoc:
        return cast(SourcesDoc, self._data("sources"))

    @property
    def settings(self) -> EngineSettingsDoc:
        return cast(EngineSettingsDoc, self._data("engine-settings"))

    @property
    def reaction_classes(self) -> ReactionClassesDoc:
        return cast(ReactionClassesDoc, self._data("reaction-classes"))

    @property
    def lfer(self) -> LferConstantsDoc:
        return cast(LferConstantsDoc, self._data("lfer-constants"))

    @property
    def radiation(self) -> RadiationChemistryDoc:
        return cast(RadiationChemistryDoc, self._data("radiation-chemistry"))

    @property
    def packages(self) -> PackagesDoc:
        return cast(PackagesDoc, self._data("packages"))

    @property
    def zones(self) -> StorageZonesDoc:
        return cast(StorageZonesDoc, self._data("storage-zones"))

    @property
    def formulary(self) -> FormularyDoc:
        return cast(FormularyDoc, self._data("formulary"))

    @property
    def stability_models(self) -> StabilityModelsDoc:
        return cast(StabilityModelsDoc, self._data("stability-models"))

    @property
    def environment_profiles(self) -> EnvironmentProfilesDoc:
        return cast(EnvironmentProfilesDoc, self._data("environment-profiles"))

    @property
    def lots(self) -> LotsDoc:
        return cast(LotsDoc, self._data("lots"))

    def pins(self) -> tuple[ConfigPin, ...]:
        """Config pins sorted by document_id (spec 1.4)."""
        return tuple(sorted((doc.pin() for doc in self.documents.values()), key=lambda p: p.document_id))


def load_bundle(config_dir: Path = DEFAULT_CONFIG_DIR, schema_dir: Path = DEFAULT_SCHEMA_DIR) -> ConfigBundle:
    """Load and validate every bundle member from ``config_dir`` and check references.
    Raises ``ConfigLoadError``, ``ConfigSchemaError`` or ``ConfigReferenceError``."""
    documents = {
        member: load_document(config_dir / file_name, f"{SCHEMA_PREFIX}{member}.schema.json", schema_dir)
        for member, file_name in BUNDLE_FILES.items()
    }
    bundle = ConfigBundle(documents=documents)
    dangling = reference_problems(bundle)
    if dangling:
        raise ConfigReferenceError("bundle", dangling)
    return bundle


# =========================================================================================
# Cross-document reference checks
# =========================================================================================


def _walk_source_ids(value: JSONValue, pointer: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{pointer}/{key}"
            if key == "source_ids" and isinstance(item, list):
                for index, sid in enumerate(item):
                    yield f"{child}/{index}", str(sid)
            else:
                yield from _walk_source_ids(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_source_ids(item, f"{pointer}/{index}")


def _duplicates(label: str, ids: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in ids:
        if item in seen:
            out.append(f"{label}: duplicate id {item!r}")
        seen.add(item)
    return out


def reference_problems(bundle: ConfigBundle) -> list[str]:
    """Every unresolved or duplicated identifier across the bundle, as readable strings
    (empty when the bundle is consistent). Checks (spec 2.3.2): every ``source_ids`` entry
    resolves in sources.json; ids are unique within their document; products reference
    known packages and reaction classes; lots reference known products, zones, packages and
    ingredients; zones reference known environment profiles; stability models reference
    known products, ingredients and packages; anchors reference known reference-study
    models; LFER series reference known reaction classes and reaction classes reference
    known LFER series."""
    problems: list[str] = []
    known_sources = {s["source_id"] for s in bundle.sources["sources"]}
    problems += _duplicates("sources", [s["source_id"] for s in bundle.sources["sources"]])
    for member, doc in sorted(bundle.documents.items()):
        for pointer, sid in _walk_source_ids(doc.data, ""):
            if sid not in known_sources:
                problems.append(f"{member}{pointer}: unknown source id {sid!r}")

    classes = bundle.reaction_classes["classes"]
    class_ids = [c["class_id"] for c in classes]
    problems += _duplicates("reaction-classes", class_ids)
    packages = [p["package_id"] for p in bundle.packages["packages"]]
    problems += _duplicates("packages", packages)
    zones = bundle.zones["zones"]
    problems += _duplicates("storage-zones", [z["zone_id"] for z in zones])
    profiles = {p["profile_id"] for p in bundle.environment_profiles["profiles"]}
    products = bundle.formulary["products"]
    problems += _duplicates("formulary", [p["product_id"] for p in products])
    models = bundle.stability_models["models"]
    problems += _duplicates("stability-models", [m["model_id"] for m in models])
    lots = bundle.lots["lots"]
    problems += _duplicates("lots", [lot["lot_id"] for lot in lots])

    hammett_ids = {s["series_id"] for s in bundle.lfer["hammett_series"]}
    taft_ids = {s["series_id"] for s in bundle.lfer["taft_series"]}
    for series in [*bundle.lfer["hammett_series"], *bundle.lfer["taft_series"]]:
        if series["class_id"] not in class_ids:
            problems.append(f"lfer-constants: series {series['series_id']!r} names unknown class")
    for ep in bundle.lfer["evans_polanyi"]:
        if ep["class_id"] not in class_ids:
            problems.append(f"lfer-constants: evans_polanyi entry names unknown class {ep['class_id']!r}")
    reference_models = {m["model_id"] for m in models if m["scope"] == "reference_study"}
    degradants_by_class: dict[str, set[str]] = {cid: set() for cid in class_ids}
    for cls in classes:
        if cls["degradant"] is not None:
            degradants_by_class[cls["class_id"]].add(cls["degradant"]["degradant_id"])
    for product in products:
        for ingredient in product["ingredients"]:
            for degradant in ingredient["degradants"]:
                degradants_by_class.setdefault(degradant["formed_by_class_id"], set()).add(
                    degradant["degradant_id"]
                )
    for cls in classes:
        for sid in cls["computed_method"]["series_ids"]:
            if sid not in hammett_ids | taft_ids:
                problems.append(f"reaction-classes: {cls['class_id']!r} names unknown LFER series {sid!r}")
        for anchor in cls["anchors"]:
            if anchor["storage_zone_id"] not in {z["zone_id"] for z in zones}:
                problems.append(
                    f"reaction-classes: anchor {anchor['anchor_id']!r} names unknown storage zone"
                )
            if anchor["package_id"] is not None and anchor["package_id"] not in packages:
                problems.append(f"reaction-classes: anchor {anchor['anchor_id']!r} names unknown package")
            ref = anchor["reference_study_id"]
            if ref is not None and ref not in reference_models:
                problems.append(
                    f"reaction-classes: anchor {anchor['anchor_id']!r} names unknown study {ref!r}"
                )
            for did in anchor.get("measured_degradant_ids", []):
                if did not in degradants_by_class[cls["class_id"]]:
                    problems.append(
                        f"reaction-classes: anchor {anchor['anchor_id']!r} "
                        f"names unknown or unrelated degradant {did!r}"
                    )

    for zone in zones:
        pid = zone["environment_profile_id"]
        if pid is not None and pid not in profiles:
            problems.append(f"storage-zones: {zone['zone_id']!r} names unknown profile {pid!r}")

    ingredients_by_product: dict[str, set[str]] = {}
    for product in products:
        if product["default_package_id"] not in packages:
            problems.append(f"formulary: {product['product_id']!r} names unknown package")
        ingredients_by_product[product["product_id"]] = set()
        for ing in product["ingredients"]:
            ingredients_by_product[product["product_id"]].add(ing["ingredient_id"])
            for deg in ing["degradants"]:
                if deg["formed_by_class_id"] not in class_ids:
                    problems.append(f"formulary: degradant {deg['degradant_id']!r} names unknown class")

    for model in models:
        if model["scope"] == "product":
            pid = model["product_id"] or ""
            if pid not in ingredients_by_product:
                problems.append(f"stability-models: {model['model_id']!r} names unknown product {pid!r}")
            elif model["ingredient_id"] not in ingredients_by_product[pid]:
                problems.append(f"stability-models: {model['model_id']!r} names unknown ingredient")
            if model["package_id"] is not None and model["package_id"] not in packages:
                problems.append(f"stability-models: {model['model_id']!r} names unknown package")
        for cid in model["covers"]:
            if cid not in class_ids:
                problems.append(f"stability-models: {model['model_id']!r} covers unknown class {cid!r}")
        allowed_degradants: set[str] = set()
        if model["scope"] == "product":
            for product in products:
                if product["product_id"] != model["product_id"]:
                    continue
                for ingredient in product["ingredients"]:
                    if ingredient["ingredient_id"] == model["ingredient_id"]:
                        allowed_degradants.update(d["degradant_id"] for d in ingredient["degradants"])
            for cls in classes:
                if cls["degradant"] is not None and (
                    cls["class_id"] in model["covers"]
                    or (cls["driver"] == "thermal" and cls["role"] == "destructive")
                ):
                    allowed_degradants.add(cls["degradant"]["degradant_id"])
        else:
            allowed_degradants = set().union(*degradants_by_class.values())
        for did in model.get("measured_degradant_ids", []):
            if did not in allowed_degradants:
                problems.append(
                    f"stability-models: {model['model_id']!r} names unknown or unrelated degradant {did!r}"
                )

    calibrations = [
        (f"total_rate_priors/{p['dose_form_family']}", p.get("calibration_provenance"))
        for p in bundle.reaction_classes["total_rate_priors"]
    ] + [(f"zones/{z['zone_id']}", z.get("rate_multiplier_calibration")) for z in zones]
    for label, calibration in calibrations:
        if calibration is not None:
            problems += _duplicates(label + "/statistics", [s["name"] for s in calibration["statistics"]])
    for prior in bundle.reaction_classes["total_rate_priors"]:
        calibration = prior.get("calibration_provenance")
        if calibration is not None and (
            calibration["vector_id"] != prior["calibration"]["vector_id"]
            or calibration["rows"] != prior["calibration"]["rows"]
        ):
            problems.append(
                f"total_rate_priors/{prior['dose_form_family']}: "
                "calibration provenance disagrees with calibration reference"
            )
    q3b = bundle.settings.get("q3b")
    if q3b is not None:
        for label, bands in (
            ("reporting", q3b["reporting"]),
            ("identification", q3b["identification"]),
            ("qualification", q3b["qualification"]),
        ):
            last_bound = 0.0
            for index, band in enumerate(bands):
                bound = band["dose_upper_mg"]
                if bound is None:
                    if index != len(bands) - 1:
                        problems.append(f"q3b/{label}: only the last band may be unbounded")
                elif bound["value"] <= last_bound:
                    problems.append(f"q3b/{label}: dose bounds must increase")
                else:
                    last_bound = bound["value"]
            if bands and bands[-1]["dose_upper_mg"] is not None:
                problems.append(f"q3b/{label}: last band must be unbounded")

    zone_ids = {z["zone_id"] for z in zones}
    for lot in lots:
        if lot["product_id"] not in ingredients_by_product:
            problems.append(f"lots: {lot['lot_id']!r} names unknown product {lot['product_id']!r}")
            continue
        for assay in lot["initial_assay"]:
            if assay["ingredient_id"] not in ingredients_by_product[lot["product_id"]]:
                problems.append(f"lots: {lot['lot_id']!r} initial assay names unknown ingredient")
        for interval in lot["custody"]:
            if interval["zone_id"] not in zone_ids:
                problems.append(f"lots: {lot['lot_id']!r} custody names unknown zone {interval['zone_id']!r}")
        for pkg in lot["package_history"]:
            if pkg["package_id"] not in packages:
                problems.append(f"lots: {lot['lot_id']!r} names unknown package {pkg['package_id']!r}")
    return problems
