"""Loads the medicines configuration at start (AGENTS 3: flight tables): treatments.json and medicines.json from the
configuration directory, the pharmacy formulary, lots and storage zones of the pharmacy bundle. Each document is
checked against its generated contract model, then by the domain checks M-L1 to M-L9; any failure raises DocumentsInvalid and
nothing loads. Interim: ConfigName does not carry these two documents yet, so they are read here, not through the
release (gap listed in the E32 report)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ValidationError

from crew_health.contracts_gen.schemas.config_medicines_schema import MedicinesJson
from crew_health.contracts_gen.schemas.config_treatments_schema import TreatmentsJson
from crew_health.medicines.adapters.potency import BUNDLE_FILES
from crew_health.medicines.domain.documents import (
    DocumentsInvalid,
    Finding,
    check,
    formulary_products,
    parse_policy,
    parse_treatments,
)
from crew_health.medicines.domain.model import FormularyProduct, MedicinesPolicy, Treatments

type Json = Mapping[str, object]
_MAX_BYTES = 8 * 1024 * 1024  # one configuration document; commands.max_bytes is the same bound


@dataclass(frozen=True, slots=True)
class Loaded:
    treatments: Treatments
    policy: MedicinesPolicy
    formulary: Mapping[str, FormularyProduct]
    lots: tuple[Json, ...]


def _no_constant(text: str) -> object:
    raise ValueError("NaN or infinity: " + text)


def _bytes(path: Path) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise DocumentsInvalid(
            (Finding("M-LOAD", str(path.name), f"unreadable: {error.__class__.__name__}"),)
        ) from error
    if len(raw) > _MAX_BYTES:
        raise DocumentsInvalid((Finding("M-LOAD", path.name, "larger than the document bound"),))
    return raw


def _parse(raw: bytes, name: str) -> tuple[Json, str]:
    if len(raw) > _MAX_BYTES:
        raise DocumentsInvalid((Finding("M-LOAD", name, "larger than the document bound"),))
    try:
        value = json.loads(raw, parse_float=Decimal, parse_constant=_no_constant)
    except (ValueError, UnicodeError) as error:
        raise DocumentsInvalid((Finding("M-LOAD", name, "not JSON"),)) from error
    if not isinstance(value, dict):
        raise DocumentsInvalid((Finding("M-LOAD", name, "not an object"),))
    return value, hashlib.sha256(raw).hexdigest()


def _shape(model: type[BaseModel], value: Json, name: str) -> None:
    try:
        model.model_validate(json.loads(json.dumps(value, default=float)))
    except ValidationError as error:
        findings = tuple(
            Finding(
                "M-SCHEMA", "/" + "/".join(str(p) for p in e.get("loc", ())), str(e.get("msg", "invalid"))
            )
            for e in error.errors()[:20]
        )
        raise DocumentsInvalid(findings) from error


def read_pharmacy(directory: Path) -> dict[str, bytes]:
    """Every pharmacy bundle member's bytes from a directory (the import reads them once, then works on bytes)."""
    return {member: _bytes(directory / name) for member, name in BUNDLE_FILES.items()}


def read_documents(config_dir: Path) -> tuple[bytes, bytes]:
    """treatments.json and medicines.json of the configuration directory."""
    return _bytes(config_dir / "treatments.json"), _bytes(config_dir / "medicines.json")


def parse(
    treatments: bytes, policy: bytes, pharmacy: Mapping[str, bytes], condition_ids: frozenset[str]
) -> Loaded:
    """The two medicines documents against the formulary, lots and storage zones of one pharmacy bundle."""
    treatments_raw, treatments_sha = _parse(treatments, "treatments.json")
    policy_raw, policy_sha = _parse(policy, "medicines.json")
    formulary_raw, _ = _parse(pharmacy["formulary"], "formulary.json")
    lots_raw, _ = _parse(pharmacy["lots"], "lots.json")
    zones_raw, _ = _parse(pharmacy["storage-zones"], "storage-zones.json")
    _shape(TreatmentsJson, treatments_raw, "treatments.json")
    _shape(MedicinesJson, policy_raw, "medicines.json")
    formulary = formulary_products(formulary_raw)
    zone_ids = frozenset(str(z["zone_id"]) for z in cast(list[Json], zones_raw.get("zones") or []))
    findings = check(treatments_raw, policy_raw, formulary, condition_ids, zone_ids)
    if findings:
        raise DocumentsInvalid(findings)
    lots = tuple(cast(list[Json], lots_raw.get("lots") or []))
    return Loaded(
        treatments=parse_treatments(treatments_raw, treatments_sha),
        policy=parse_policy(policy_raw, policy_sha),
        formulary=formulary,
        lots=lots,
    )


def load(config_dir: Path, condition_ids: frozenset[str]) -> Loaded:
    treatments, policy = read_documents(config_dir)
    return parse(treatments, policy, read_pharmacy(config_dir / "pharmacy"), condition_ids)
