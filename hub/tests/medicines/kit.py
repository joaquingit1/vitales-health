"""Domain-level builders over the repository's own medicines configuration (rule 1: fixtures come from
configuration). Expected clinical values are never computed here; the golden tests state them from the sources."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from functools import cache

from crew_health.medicines.adapters.documents import Loaded, load
from crew_health.medicines.domain.model import (
    ContextPart,
    DoseContext,
    DoseRequest,
    LotSummary,
    LotView,
    MedicationContext,
    Mode,
    PastAdministration,
    Potency,
    PotencyPolicy,
    WorkingDiagnosis,
)
from crew_health.medicines.domain.units import Amount

from .harness import CONFIG, NOW

SUBJECT = "person_a"


@cache
def condition_ids() -> frozenset[str]:
    raw = json.loads((CONFIG / "kit-map.json").read_text(encoding="utf-8"))
    return frozenset(str(c["condition_id"]) for c in raw["conditions"])


@cache
def loaded() -> Loaded:
    return load(CONFIG, condition_ids())


def part(
    state: str, items: Sequence[Mapping[str, object]] = (), *, age: timedelta = timedelta(hours=1)
) -> ContextPart:
    return ContextPart(state, tuple(items), NOW - age, f"stmt_{state}_{len(items)}")


def complete_context(**parts: ContextPart | None) -> MedicationContext:
    """Every part documented and fresh: no allergy known, no other medicine, organs normal, no condition."""
    base: dict[str, ContextPart | None] = {
        "allergies": part("none_known"),
        "other_medicines": part("none"),
        "renal": part("normal"),
        "hepatic": part("normal"),
        "conditions": part("none"),
    }
    base.update(parts)
    return MedicationContext(**base)


def lot(
    lot_id: str,
    product_id: str,
    *,
    quantity: int = 30,
    status: str = "active",
    expiry: datetime = datetime(2029, 6, 23, tzinfo=NOW.tzinfo),
    adverse: bool = False,
    summary: LotSummary | None = None,
) -> LotView:
    return LotView(lot_id, product_id, status, expiry, Decimal(quantity), adverse, summary)


def lots() -> tuple[LotView, ...]:
    return (
        lot("lot-apap-001", "acetaminophen_325_tab"),
        lot("lot-ibu-001", "ibuprofen_400_tab"),
        lot("lot-pmz-tab-001", "promethazine_25_tab"),
    )


def given(
    *,
    history: Sequence[PastAdministration] = (),
    context: MedicationContext | None = None,
    lot_views: Sequence[LotView] | None = None,
    mode: Mode = "SIM",
    now: datetime = NOW,
    clock_discontinuity: bool = False,
    max_window_rows: int = 10000,
) -> DoseContext:
    docs = loaded()
    return DoseContext(
        now=now,
        mode=mode,
        treatments=docs.treatments,
        policy=docs.policy,
        formulary=docs.formulary,
        lots=tuple(lot_views if lot_views is not None else lots()),
        history=tuple(history),
        context=context if context is not None else complete_context(),
        clock_discontinuity=clock_discontinuity,
        max_window_rows=max_window_rows,
    )


def ask(
    product_id: str, condition_id: str, *, lot_id: str | None = None, lineage: str = "medd_1"
) -> DoseRequest:
    return DoseRequest(SUBJECT, WorkingDiagnosis(lineage, condition_id, lineage), product_id, lot_id, False)


def past(
    hours_ago: float,
    ingredient_id: str,
    mg: int | Decimal,
    *,
    rule_id: str,
    lineage: str = "medd_1",
    n: int = 0,
) -> PastAdministration:
    return PastAdministration(
        f"meda_{rule_id}_{n}_{hours_ago}",
        NOW - timedelta(hours=hours_ago),
        rule_id,
        lineage,
        {ingredient_id: Amount(Decimal(mg), "mg")},
    )


def assessed(
    state: str, low: str, median: str, high: str, ingredient_id: str, *, eligible: bool = True
) -> LotSummary:
    return LotSummary(
        run_id="medr_1",
        as_of=NOW - timedelta(hours=1),
        state=state,  # type: ignore[arg-type]
        potency={ingredient_id: Potency(Decimal(low), Decimal(median), Decimal(high))},
        adjustment_eligible=eligible,
        assay_used=False,
        custody_declared_or_gapped=False,
        engine_version="0.1.0",
    )


def with_policy(ctx: DoseContext, **potency: object) -> DoseContext:
    return replace(ctx, policy=replace(ctx.policy, potency=replace(ctx.policy.potency, **potency)))  # type: ignore[arg-type]


def switched(policy: PotencyPolicy, name: str) -> PotencyPolicy:
    """The policy with one boolean switch turned on, by name (the tests iterate over switch names)."""
    if not isinstance(getattr(policy, name), bool):
        raise ValueError(name)
    return replace(policy, **{name: True})  # type: ignore[arg-type]
