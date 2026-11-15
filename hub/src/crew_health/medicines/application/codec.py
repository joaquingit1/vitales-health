"""JSON forms of medicines values stored in record bodies and sent on the wire (UCUM amounts as decimal numbers)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import cast

from crew_health.medicines.domain.model import (
    ContextPart,
    LotState,
    LotSummary,
    Potency,
    Proposal,
)
from crew_health.medicines.domain.units import Amount

type Json = Mapping[str, object]

_MAX_DEPTH = 16


def decimal_text(value: Decimal) -> str:
    """Canonical exact text of a finite Decimal, never through a float and never in exponent form."""
    if not value.is_finite():
        raise ValueError("non-finite number")
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "0") else text


def dumps(value: object) -> str:
    """Compact sorted JSON in which a Decimal keeps its exact text. Bounded depth, no recursion."""
    out: list[str] = []
    stack: list[tuple[object, int] | str] = [(value, 0)]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            continue
        node, depth = item
        if depth > _MAX_DEPTH:
            raise ValueError("JSON nesting past the bound")
        if node is None or isinstance(node, bool | int | str):
            out.append(json.dumps(node))
        elif isinstance(node, Decimal):
            out.append(decimal_text(node))
        elif isinstance(node, Mapping):
            parts: list[tuple[object, int] | str] = ["{"]
            for i, key in enumerate(sorted(node)):
                parts.append(("," if i else "") + json.dumps(str(key)) + ":")
                parts.append((node[key], depth + 1))
            parts.append("}")
            stack.extend(reversed(parts))
        elif isinstance(node, Sequence):
            seq: list[tuple[object, int] | str] = ["["]
            for i, element in enumerate(node):
                if i:
                    seq.append(",")
                seq.append((element, depth + 1))
            seq.append("]")
            stack.extend(reversed(seq))
        else:
            raise TypeError(f"not JSON: {type(node).__name__}")
    return "".join(out)


def loads(text: str) -> dict[str, object]:
    """Parse a stored body: every non-integer number becomes a Decimal (never a float)."""
    value = json.loads(text, parse_float=Decimal)
    if not isinstance(value, dict):
        raise ValueError("a stored body is an object")
    return value


def instant(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def parse_instant(text: object) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def amount_json(amount: Amount) -> dict[str, object]:
    return {"value": amount.value, "ucum": amount.ucum}


def amount_from(raw: object) -> Amount:
    data = cast(Json, raw)
    return Amount(Decimal(str(data["value"])), str(data["ucum"]))


def summary_json(summary: LotSummary) -> dict[str, object]:
    return {
        "run_id": summary.run_id,
        "as_of": instant(summary.as_of),
        "state": summary.state,
        "potency": {k: [str(v.low), str(v.median), str(v.high)] for k, v in sorted(summary.potency.items())},
        "adjustment_eligible": summary.adjustment_eligible,
        "assay_used": summary.assay_used,
        "custody_declared_or_gapped": summary.custody_declared_or_gapped,
        "engine_version": summary.engine_version,
        "alternative_lot_ids": list(summary.alternative_lot_ids),
        "alternative_product_ids": list(summary.alternative_product_ids),
    }


def summary_from(raw: Json) -> LotSummary:
    potency = cast(Mapping[str, list[str]], raw["potency"])
    return LotSummary(
        run_id=str(raw["run_id"]),
        as_of=parse_instant(raw["as_of"]),
        state=cast(LotState, raw["state"]),
        potency={k: Potency(Decimal(v[0]), Decimal(v[1]), Decimal(v[2])) for k, v in potency.items()},
        adjustment_eligible=bool(raw["adjustment_eligible"]),
        assay_used=bool(raw["assay_used"]),
        custody_declared_or_gapped=bool(raw["custody_declared_or_gapped"]),
        engine_version=str(raw["engine_version"]),
        alternative_lot_ids=tuple(str(x) for x in cast(list[object], raw["alternative_lot_ids"])),
        alternative_product_ids=tuple(str(x) for x in cast(list[object], raw["alternative_product_ids"])),
    )


def context_part(body: Json, statement_id: str, at: datetime) -> ContextPart:
    items = []
    for item in cast(list[Mapping[str, object]], body.get("items") or []):
        parsed = dict(item)
        for key in ("since", "until"):
            if isinstance(parsed.get(key), str):
                parsed[key] = parse_instant(parsed[key])
        items.append(parsed)
    return ContextPart(str(body["state"]), tuple(items), at, statement_id)


def proposal_wire(
    proposal: Proposal,
    *,
    proposal_id: str,
    subject_person_id: str,
    working_diagnosis_id: str,
    created_at: datetime,
    expires_at: datetime,
    origin_mode: str,
    state: str,
) -> dict[str, object]:
    refusal = proposal.refusal
    return {
        "proposal_id": proposal_id,
        "revision": 1,
        "outcome": proposal.outcome,
        "state": state,
        "subject_person_id": subject_person_id,
        "working_diagnosis_id": working_diagnosis_id,
        "product_id": proposal.product_id,
        "lot_id": proposal.lot_id,
        "amount": None if proposal.amount is None else amount_json(proposal.amount),
        "amount_text": proposal.amount_text,
        "ingredients": [
            {
                "ingredient_id": x.ingredient_id,
                "target": amount_json(x.target),
                "accounting": amount_json(x.accounting),
                "expected_median": None if x.expected_median is None else amount_json(x.expected_median),
                "expected_low": None if x.expected_low is None else amount_json(x.expected_low),
                "remaining_allowance": amount_json(x.remaining_allowance),
            }
            for x in proposal.ingredients
        ],
        "potency": {
            "path": proposal.path,
            "live_authorization": None
            if proposal.live_authorized_at is None
            else {"product_id": proposal.product_id, "authorized_at": proposal.live_authorized_at},
            "status": proposal.potency_status,
            "assessment_run_id": proposal.assessment_run_id,
            "intervals": [
                {"ingredient_id": k, "low": v.low, "median": v.median, "high": v.high}
                for k, v in sorted(proposal.intervals.items())
            ],
        },
        "refusal": None
        if refusal is None
        else {
            "code": refusal.code,
            "detail_text": refusal.detail_text,
            "alternatives": {"lot_ids": list(refusal.lot_ids), "product_ids": list(refusal.product_ids)},
        },
        "alerts": [
            {
                "alert_id": a.alert_id,
                "kind": a.kind,
                "level": a.level,
                "text": a.text,
                "reason_codes": list(a.reason_codes),
                "override_roles": list(a.override_roles),
            }
            for a in proposal.alerts
        ],
        "trace": [{"step": t.step, "source": t.source, "value_text": t.value_text} for t in proposal.trace],
        "review_state": proposal.review_state,
        "next_dose_earliest_at": None
        if proposal.next_dose_earliest_at is None
        else instant(proposal.next_dose_earliest_at),
        "created_at": instant(created_at),
        "expires_at": instant(expires_at),
        "origin_mode": origin_mode,
        "notice": "Engine proposal from reviewed formulary rules only; a person confirms. Never medical advice.",
    }
