"""Use case results to the generated wire models (contracts KitMapView and KitMapWhatIf, CONTRACTS 21 and 22)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel

from crew_health.contracts_gen.schemas.kit_map_view_schema import KitMapView
from crew_health.contracts_gen.schemas.kit_map_what_if_schema import KitMapWhatIf
from crew_health.kitmap.adapters.d22 import replace_ids, stand_ins
from crew_health.kitmap.application.ports import PlanColumns
from crew_health.kitmap.application.queries import ViewResult
from crew_health.kitmap.application.whatif import WhatIfResult
from crew_health.kitmap.domain.model import ConditionEvaluation, RegionSummary, StateCounts


def wire(model: type[BaseModel], body: dict[str, object], decimals: Sequence[Decimal] = ()) -> bytes:
    """Validate against the generated model and serialize; D22 suffixed condition IDs pass through unchanged.

    The generated model types supply quantities as float; `decimals` (the what-if quantities in request order) are
    written back as their exact decimal text, so no value crosses the wire through a float (rule 8, E27 4.3 step 6).
    """
    mapping = stand_ins(body)
    validated = model.model_validate(replace_ids(body, mapping))
    dumped = validated.model_dump(mode="json", by_alias=True)
    restored = replace_ids(dumped, {stand_in: original for original, stand_in in mapping.items()})
    tokens = [f"{DECIMAL_TOKEN}{i}" for i in range(len(decimals))]
    if isinstance(restored, dict) and decimals:
        supplies = restored.get("added_supplies")
        if not isinstance(supplies, list) or len(supplies) != len(decimals):
            raise ValueError("added_supplies do not match the request quantities")
        restored["added_supplies"] = [{**s, "quantity": t} for s, t in zip(supplies, tokens, strict=True)]
    text = json.dumps(restored, ensure_ascii=False, separators=(",", ":"))
    for token, value in zip(tokens, decimals, strict=True):
        text = text.replace(json.dumps(token), _decimal_text(value), 1)
    return text.encode("utf-8")


def _decimal_text(value: Decimal) -> str:
    """A JSON number with the request's digits (finite and positive, checked by RunWhatIf)."""
    if not value.is_finite():
        raise ValueError("non-finite quantity")
    return str(value)


DECIMAL_TOKEN = "\u0000kitmap_decimal_"


def wire_instant(at: datetime) -> str:
    """RFC 3339 UTC with Z (common.schema instant); a naive instant is a defect, never assumed UTC."""
    if at.tzinfo is None:
        raise ValueError("naive instant")
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _counts(counts: StateCounts) -> dict[str, int]:
    return {
        "measured_here": counts.measured_here,
        "report_only": counts.report_only,
        "needs_test_not_aboard": counts.needs_test_not_aboard,
        "total": counts.total,
    }


def _regions(regions: Sequence[RegionSummary]) -> list[dict[str, object]]:
    return [
        {"region": r.region, "counts": _counts(r.counts), "marks": [m.value for m in r.marks]}
        for r in regions
    ]


def _json_or_none(text: str | None) -> object:
    return json.loads(text) if text is not None else None


def _condition(
    evaluation: ConditionEvaluation, missing_text: str | None, columns: PlanColumns
) -> dict[str, object]:
    row = evaluation.row
    return {
        "condition_id": row.condition_id,
        "name": row.name,
        "state": evaluation.state.value,
        "body_regions": list(row.body_regions),
        "tests": [
            {
                "index": t.index,
                "name": test.name,
                "instruments": list(t.instruments),
                "needs_no_instrument": t.needs_no_instrument,
                "fully_aboard": t.fully_aboard,
                "missing": list(t.missing),
            }
            for t, test in zip(evaluation.tests, row.tests, strict=True)
        ],
        "missing_instruments": list(evaluation.missing_instruments),
        "missing_text": missing_text,
        "report_only_findings": list(row.report_only_findings),
        "cliff_url": row.cliff_url,
        "review_state": row.review_state,
        "plan_id": columns.plan_id,
        "plan_written_for": columns.written_for,
        "plan_aboard": columns.plan_aboard,
        "ground_needs": _json_or_none(row.ground_needs_json),
        "treatment_scope": _json_or_none(row.treatment_scope_json),
        "rtdc": _json_or_none(row.rtdc_json),
    }


def kit_map_view(result: ViewResult) -> bytes:
    ctx = result.ctx
    doc = ctx.release.doc
    kit = doc.kits[ctx.environment_id]
    conditions = [
        _condition(e, text, columns)
        for e, text, columns in zip(result.evaluations, result.missing_texts, result.columns, strict=True)
    ]
    return wire(
        KitMapView,
        {
            "environment_id": ctx.environment_id,
            "kit_map_version": doc.version,
            "kit_map_label": doc.label,
            "example": doc.example,
            "kit": [{"instrument_id": i.id, "name": i.name} for i in doc.instruments if i.id in kit],
            "candidates": [
                {"instrument_id": c, "name": _name(result, c), "aboard": c in kit} for c in doc.candidates
            ],
            "counts": _counts(result.counts),
            "regions": _regions(result.regions),
            "conditions": conditions,
            "plans_aboard": sum(1 for c in result.columns if c.plan_aboard),
            "plans_written_for_condition": sum(1 for c in result.columns if c.written_for == "condition"),
            "plans_from_family": sum(1 for c in result.columns if c.written_for == "family"),
            "plans_to_write": [
                e.row.condition_id
                for e, c in zip(result.evaluations, result.columns, strict=True)
                if not c.plan_aboard
            ],
            "as_of": wire_instant(ctx.at),
        },
    )


def _name(result: ViewResult, instrument_id: str) -> str:
    instrument = result.ctx.release.doc.instrument(instrument_id)
    return instrument.name if instrument is not None else instrument_id


def kit_map_what_if(result: WhatIfResult) -> bytes:
    ctx = result.after.ctx
    return wire(
        KitMapWhatIf,
        {
            "environment_id": ctx.environment_id,
            "kit_map_version": ctx.release.doc.version,
            "added_instrument_ids": list(result.added_instrument_ids),
            "ignored_instrument_ids": list(result.ignored_instrument_ids),
            "base_counts": _counts(result.base.counts),
            "what_if_counts": _counts(result.after.counts),
            "regions": _regions(result.after.regions),
            "changed": [
                {
                    "condition_id": before.row.condition_id,
                    "name": before.row.name,
                    "from": before.state.value,
                    "to": after.state.value,
                    "body_regions": list(before.row.body_regions),
                    "cliff_url": before.row.cliff_url,
                }
                for before, after in result.changed
            ],
            "added_supplies": [{"supply_id": s, "quantity": q} for s, q in result.added_supplies],
            "added_skills": [{"skill_id": s, "person_ids": list(p)} for s, p in result.added_skills],
            "plans_changed": [
                {
                    "plan_id": p.plan_id,
                    "condition_id": p.condition_id,
                    "flow_id": p.flow_id,
                    "from": {
                        "plan_aboard": p.before_plan_aboard,
                        "waits_for_medical_officer": p.before_waits_for_mo,
                    },
                    "to": {
                        "plan_aboard": p.after_plan_aboard,
                        "waits_for_medical_officer": p.after_waits_for_mo,
                    },
                }
                for p in result.plans_changed
            ],
            "cover_changed": [
                {"supply_id": c.supply_id, "from_uses": c.from_uses, "to_uses": c.to_uses}
                for c in result.cover_changed
            ],
            "persisted": False,
        },
        decimals=tuple(q for _, q in result.added_supplies),
    )
