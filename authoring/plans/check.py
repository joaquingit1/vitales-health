"""Authoring acceptance for config/onboard-plans.json (E28 8.3). Run with the hub's environment:

    uv run --project hub python authoring/plans/check.py

Hard checks (exit 1 on any failure):
1. config/onboard-plans.json and assignments.csv equal build.py output byte for byte.
2. The document validates against contracts/api/schemas/config-onboard-plans.schema.json, with only the
   condition ID pattern widened in memory for coordinator decision D22 (ICL36A, ICL36B).
3. The hub's own loader (crew_health.plans.domain parse_document and check_plans, checks 14 and 15, LIVE
   mode) finds nothing against config/kit-map.json, config/skills-catalog.json and
   config/responder-flows.json, and every kit-map row has a plan aboard in every environment (no dead end).
4. Every kit-map row has plan_<lower(condition_id)>; the demo rows are written for the condition, the rest
   family; every responder flow has plan_<flow_id>.

Reported, never silently passed: plans without a recorded independent check (checked_by null), and the
E28-03 gate (form values and row_checks.csv), which stays "not done" while the kit-map form fields are null
(E27 8.3 item 3).

The kit-map rows' plan_id is null until E27's build writes it; check 15 here reads each row's plan by the
same rule E27 uses ("plan_" + lower(condition_id)) and says so in its output.
"""

from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import cast

import jsonschema
from referencing import Registry, Resource

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import build  # noqa: E402
from crew_health.kernel.units import Quantity, Unit  # noqa: E402
from crew_health.plans.domain.availability import StockLevel  # noqa: E402
from crew_health.plans.domain.model import SupplyInfo, parse_document  # noqa: E402
from crew_health.plans.domain.release_checks import (  # noqa: E402
    CatalogFacts,
    KitFacts,
    ReleaseFacts,
    check_plans,
    plan_rows_aboard,
)

SCHEMAS = ROOT / "contracts/api/schemas"
CONTRACT_PATTERN = "^ICL[1-9][0-9]{0,2}$"
D22_PATTERN = "^ICL[1-9][0-9]{0,2}[A-Z]?$"
DEMO_ROWS = 7
# Authoring bounds: the hub settings plans.max_plans_per_document and plans.max_steps_per_plan apply at
# activation;
# these are the maxima their SettingSpecs allow, so the authoring check never accepts what a hub could not.
MAX_PLANS = 1000
MAX_STEPS = 200
UCUM_CODES = frozenset({"1", "mL", "L", "g", "mg", "kg", "m", "cm"})

type Obj = dict[str, object]


def _schema(path: Path) -> Obj:
    text = path.read_text(encoding="utf-8")
    return cast(
        Obj, json.loads(text.replace(json.dumps(CONTRACT_PATTERN)[1:-1], json.dumps(D22_PATTERN)[1:-1]))
    )


def schema_errors(document: object) -> list[str]:
    registry: Registry = Registry().with_resources(
        (path.name, Resource.from_contents(_schema(path))) for path in SCHEMAS.glob("*.schema.json")
    )
    validator = jsonschema.Draft202012Validator(
        _schema(SCHEMAS / "config-onboard-plans.schema.json"), registry=registry
    )
    return sorted(f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(document))


def _load(path: Path) -> Obj:
    return cast(Obj, json.loads(path.read_text(encoding="utf-8")))


def kit_supplies(kit_map: Obj) -> dict[str, SupplyInfo]:
    """kit-map supplies[] as the loader reads them. A unit word outside the small UCUM allowlist becomes the
    UCUM annotation {word} (E32's mechanical rule)."""
    result: dict[str, SupplyInfo] = {}
    for raw in cast(list[Obj], kit_map.get("supplies", [])):
        word = str(raw["unit"])
        unit = Unit(word if word in UCUM_CODES else "{" + word + "}")
        result[str(raw["supply_id"])] = SupplyInfo(str(raw["supply_id"]), unit, raw.get("consumable") is True)
    return result


def packed_stock(kit_map: Obj, supplies: dict[str, SupplyInfo]) -> dict[str, dict[str, StockLevel]]:
    stock: dict[str, dict[str, StockLevel]] = {}
    for env, rows in cast(dict[str, list[Obj]], kit_map.get("stock", {})).items():
        stock[env] = {}
        for row in rows:
            supply_id = str(row["supply_id"])
            quantity = row.get("quantity")
            info = supplies.get(supply_id)
            amount = (
                Quantity(Decimal(str(quantity)), info.unit)
                if isinstance(quantity, int | float) and info is not None
                else None
            )
            stock[env][supply_id] = StockLevel(supply_id, True, amount)
    return stock


def release_facts(kit_map: Obj) -> ReleaseFacts:
    supplies = kit_supplies(kit_map)
    skills = _load(ROOT / "config/skills-catalog.json")
    flows = _load(ROOT / "config/responder-flows.json")
    rows = tuple(
        (
            str(r["condition_id"]),
            cast(str | None, r.get("plan_id")) or build.plan_id_for(str(r["condition_id"])),
        )
        for r in cast(list[Obj], kit_map["conditions"])
    )
    return ReleaseFacts(
        kit=KitFacts(
            instrument_ids=frozenset(str(i["id"]) for i in cast(list[Obj], kit_map["instruments"])),
            supplies=supplies,
            kits={env: frozenset(cast(list[str], ids)) for env, ids in cast(Obj, kit_map["kits"]).items()},
            stock=packed_stock(kit_map, supplies),
            rows=rows,
        ),
        catalog=CatalogFacts(
            skill_ids=frozenset(str(s["skill_id"]) for s in cast(list[Obj], skills.get("skills", []))),
            landmark_ids=frozenset(
                str(s["landmark_id"]) for s in cast(list[Obj], skills.get("landmarks", []))
            ),
            teach_texts=(),
        ),
        flow_ids=frozenset(str(f["id"]) for f in cast(list[Obj], flows.get("flows", []))),
        return_options={},
        quote_verified={},
        rtdc_verified={},
        mode="LIVE",
        max_plans_per_document=MAX_PLANS,
        max_steps_per_plan=MAX_STEPS,
    )


def main() -> int:
    failures: list[str] = []
    text, assignments = build.build()
    if (
        build.OUTPUT.read_text(encoding="utf-8") != text
        or build.ASSIGNMENTS.read_text(encoding="utf-8") != assignments
    ):
        failures.append("1: config/onboard-plans.json or assignments.csv differs from build.py output")
    document = json.loads(text)
    errors = schema_errors(document)
    failures += [f"2: schema {e}" for e in errors[:20]]

    kit_map = _load(ROOT / "config/kit-map.json")
    facts = release_facts(kit_map)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    parsed = parse_document(document, document_sha256=digest, supplies=facts.kit.supplies)
    findings = check_plans(parsed, document, facts)
    failures += [
        f"3: check {f.check_id} {f.pointer}: {f.message}" for f in findings if f.severity == "reject"
    ][:40]
    aboard, total = plan_rows_aboard(parsed, facts)
    if aboard != total or total != len(cast(list[object], kit_map["conditions"])):
        failures.append(f"3: plan aboard in every environment for {aboard} of {total} rows")

    plans = {str(p["plan_id"]): p for p in document["plans"]}
    rows = [str(r["condition_id"]) for r in cast(list[Obj], kit_map["conditions"])]
    missing = [r for r in rows if build.plan_id_for(r) not in plans]
    if missing:
        failures.append(f"4: rows without a plan: {missing}")
    flow_ids = [str(f["id"]) for f in cast(list[Obj], _load(ROOT / "config/responder-flows.json")["flows"])]
    missing_flows = [f for f in flow_ids if "plan_" + f not in plans]
    if missing_flows:
        failures.append(f"4: responder flows without a plan: {missing_flows}")
    kinds = [plans[build.plan_id_for(r)]["written_for"] for r in rows if r not in missing]
    if kinds.count("condition") != DEMO_ROWS or kinds.count("family") != len(kinds) - DEMO_ROWS:
        failures.append(
            f"4: written_for counts condition={kinds.count('condition')} family={kinds.count('family')}"
        )

    unchecked = sorted(pid for pid, p in plans.items() if not p.get("checked_by"))
    unwritten = sorted(pid for pid, p in plans.items() if not p.get("written_by"))
    print(f"rows {len(rows)}; plans {len(plans)}; aboard in all environments {aboard} of {total}")
    print(
        "kit-map plan_id: null on every row until E27 writes it; read by the rule plan_<lower(condition_id)>"
    )
    print(f"independently checked plans: {len(plans) - len(unchecked)} of {len(plans)}")
    print(f"plans without written_by: {len(unwritten)} ({', '.join(unwritten)})")
    print(
        "E28-03 gate: not done (kit-map ground_needs, treatment_scope and rtdc are null;"
        " no row_checks.csv yet)"
    )
    for failure in failures:
        print("FAIL " + failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
