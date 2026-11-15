"""Validation and mapping of the engine's InventoryAssessment before anything is published (E32 5.3).

A successful exit is not a valid result. The whole document is checked against the engine's own schema (copied from
`contracts/pharmacy`, kept equal by a test, because the hub never imports pharmacy), every number must be finite, the
as-of must be the run's, and the lots and ingredients must be exactly the ones the run's bundle holds: one assessment
per lot, one estimate per ingredient of its product, with a median and ordered quantiles. Anything else raises
ResultInvalid, the run fails `result_invalid` and the last completed assessment, with its exclusions, stays in use."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.medicines.application.ports import ResultInvalid
from crew_health.medicines.domain.model import LotState, LotSummary, Potency

type Json = Mapping[str, object]

SCHEMA_DIR = Path(__file__).with_name("engine_schema")
MAX_NODES = 2_000_000  # bounded walk; a result is already bounded by the run's output size


def validator() -> Draft202012Validator:
    """Loaded once at startup (AGENTS 3.3)."""
    common = json.loads((SCHEMA_DIR / "common.schema.json").read_text(encoding="utf-8"))
    inventory = json.loads((SCHEMA_DIR / "inventory-assessment.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
    return Draft202012Validator(inventory, registry=registry)


def _no_constant(name: str) -> object:
    raise ResultInvalid(f"non-finite number {name}")


def _finite(document: object) -> None:
    stack: list[object] = [document]
    for _ in range(MAX_NODES):
        if not stack:
            return
        node = stack.pop()
        if isinstance(node, float) and not math.isfinite(node):
            raise ResultInvalid("non-finite number")
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    raise ResultInvalid("result too large to check")


def expected_identities(bundle: Mapping[str, bytes]) -> dict[str, frozenset[str]]:
    """Every lot of the run's bundle with the ingredient IDs of its product (lots.json, formulary.json)."""
    lots = cast(Json, json.loads(bundle["lots"]))
    formulary = cast(Json, json.loads(bundle["formulary"]))
    ingredients = {
        str(p["product_id"]): frozenset(
            str(i["ingredient_id"]) for i in cast(Sequence[Json], p["ingredients"])
        )
        for p in cast(Sequence[Json], formulary["products"])
    }
    return {
        str(lot["lot_id"]): ingredients.get(str(lot["product_id"]), frozenset())
        for lot in cast(Sequence[Json], lots["lots"])
    }


def _potency(estimate: Json) -> Potency:
    quantiles = cast(Sequence[Json], estimate["quantiles"])
    pairs = sorted((float(cast(float, q["probability"])), float(cast(float, q["value"]))) for q in quantiles)
    probabilities, values = [p for p, _ in pairs], [v for _, v in pairs]
    if not pairs or probabilities != sorted(set(probabilities)) or values != sorted(values):
        raise ResultInvalid(f"{estimate['lot_id']}/{estimate['ingredient_id']}: quantiles not ordered")
    if any(not 0 < p < 1 for p, _ in pairs) or pairs[0][1] < 0:
        raise ResultInvalid(f"{estimate['lot_id']}/{estimate['ingredient_id']}: quantile out of range")
    median = [v for p, v in pairs if p == 0.5]
    if not median:
        raise ResultInvalid(f"{estimate['lot_id']}/{estimate['ingredient_id']}: no median")
    return Potency(
        low=Decimal(str(pairs[0][1])) / 100,
        median=Decimal(str(median[0])) / 100,
        high=Decimal(str(pairs[-1][1])) / 100,
    )


def _declared(estimates: Sequence[Json]) -> bool:
    for estimate in estimates:
        exposure = cast(Json, estimate.get("exposure") or {})
        for row in cast(Sequence[Json], exposure.get("coverage") or ()):
            if (
                float(cast(float, row.get("declared_s", 0))) > 0
                or float(cast(float, row.get("gap_s", 0))) > 0
            ):
                return True
    return False


def _summary(run_id: str, assessment: Json, ingredients: frozenset[str]) -> LotSummary:
    lot_id = str(assessment["lot_id"])
    decision = cast(Json, assessment["decision"])
    estimates = cast(Sequence[Json], assessment["estimates"])
    if str(decision["lot_id"]) != lot_id or any(str(e["lot_id"]) != lot_id for e in estimates):
        raise ResultInvalid(f"{lot_id}: an estimate or decision names another lot")
    named = [str(e["ingredient_id"]) for e in estimates]
    if len(named) != len(set(named)) or set(named) != ingredients:
        raise ResultInvalid(f"{lot_id}: estimates {sorted(named)} for ingredients {sorted(ingredients)}")
    adjustment = cast(Json, decision["adjustment"])
    pins = cast(Json, decision["pins"])
    return LotSummary(
        run_id=run_id,
        as_of=datetime.fromisoformat(str(decision["as_of_utc"]).replace("Z", "+00:00")),
        state=cast(LotState, decision["state"]),
        potency={str(e["ingredient_id"]): _potency(e) for e in estimates},
        adjustment_eligible=bool(adjustment["eligible"]),
        assay_used=bool(adjustment["assay_ids_used"]),
        custody_declared_or_gapped=_declared(estimates),
        engine_version=str(pins.get("engine_version") or "unknown"),
        alternative_lot_ids=tuple(str(x) for x in cast(list[object], decision["alternative_lot_ids"])),
        alternative_product_ids=tuple(
            str(x) for x in cast(list[object], decision["alternative_product_ids"])
        ),
    )


def summaries(
    schema: Draft202012Validator,
    run_id: str,
    result: bytes,
    bundle: Mapping[str, bytes],
    as_of: datetime,
) -> dict[str, LotSummary]:
    """One LotSummary per lot of the bundle, or ResultInvalid. Potency: the outermost quantiles as low and high and
    the 0.5 quantile as median, as fractions of label claim."""
    try:
        document = json.loads(result, parse_constant=_no_constant)
        expected = expected_identities(bundle)
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        raise ResultInvalid(f"unreadable: {type(error).__name__}") from error
    _finite(document)
    found = next(iter(sorted(schema.iter_errors(document), key=lambda e: e.json_path)), None)
    if found is not None:
        raise ResultInvalid(f"schema: {found.json_path}: {found.message[:200]}")
    inventory = cast(Json, document)
    stated = datetime.fromisoformat(str(inventory["as_of_utc"]).replace("Z", "+00:00"))
    if stated != as_of:
        raise ResultInvalid(f"as_of {inventory['as_of_utc']} is not the run's")
    out: dict[str, LotSummary] = {}
    for assessment in cast(Sequence[Json], inventory["assessments"]):
        lot_id = str(assessment["lot_id"])
        if lot_id in out or lot_id not in expected:
            raise ResultInvalid(f"{lot_id}: duplicate or not in the bundle")
        out[lot_id] = _summary(run_id, assessment, expected[lot_id])
    missing = sorted(set(expected) - set(out))
    if missing:
        raise ResultInvalid("lots missing from the result: " + ", ".join(missing[:10]))
    return out
