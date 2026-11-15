"""Build config/onboard-plans.json from the authoring inputs (E28 5.3).

Deterministic: the same inputs give the same bytes. Inputs, all in the repository:

- authoring/plans/doc.json: version label, example flag, notice and the sources not yet in the package
  registry;
- authoring/plans/conditions/plan_icl<n>.json: plans written for one condition (the seven demo rows);
- authoring/plans/flows/plan_<flow_id>.json: plans written for a responder flow, in
  config/responder-flows.json order;
- authoring/plans/family-template.json: the family template (a standard and an urgent variant, chosen by its
  `urgent_when` words over the row's assessment text), expanded for every other kit-map row with that row's
  assessment family fields quoted verbatim from docs/product/assessment-families.json;
- docs/product/sources.csv: the registry; a source that supports only inventory membership is not cited for
  a step;
- config/kit-map.json: the condition rows, their order and their names (plan titles);
- docs/product/condition-assessments-119.json: the family of each row;
- authoring/plans/adjudication/check.json: the checking model's verdict per plan, keyed by plan_id. A plan
  whose verdict is not `agrees` keeps `checked_by: null` and the plan says so; nothing is invented to fill
  it.

Outputs: config/onboard-plans.json and authoring/plans/assignments.csv (plan_id, written_by, checked_by).
Plan IDs follow `"plan_" + lower(condition_id)`, the rule E27's build uses for the kit-map row's plan_id.

Usage: uv run --project hub python authoring/plans/build.py [--check]
(--check fails when the outputs on disk differ)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

from crew_health.plans.domain.text_check import find_amounts, medicine_words

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "authoring/plans"
OUTPUT = ROOT / "config/onboard-plans.json"
ASSIGNMENTS = HERE / "assignments.csv"
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
SOURCE_ID = re.compile(r"^(EV|RT|RS|SK|S|C|N|L)\d{2}$")
FAMILY_FIELDS = ("optional_inputs", "exam", "limitations")
ROW_FIELDS = ("confirming_tests",)
AGREES = "agrees"
INVENTORY_ONLY = "NASA inventory membership"

type Obj = dict[str, object]


class BuildError(Exception):
    pass


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _obj(value: object, where: str) -> Obj:
    if not isinstance(value, dict):
        raise BuildError(f"{where}: object expected")
    return value


def _list(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise BuildError(f"{where}: list expected")
    return value


def _str(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise BuildError(f"{where}: non-empty string expected")
    return value


def plan_id_for(condition_id: str) -> str:
    return "plan_" + condition_id.lower()


def family_sources(family: Obj, clinical: frozenset[str]) -> list[str]:
    """The family's registered sources that support clinical content. A source the registry marks as
    supporting only NASA inventory membership (S01) is not cited for a step
    (adjudication/check-pass1.json)."""
    raw = family.get("source_ids")
    words = re.split(r"[;,\s]+", raw) if isinstance(raw, str) else []
    return sorted(word for word in words if SOURCE_ID.match(word) and word in clinical)


def clinical_sources() -> frozenset[str]:
    with (ROOT / "docs/product/sources.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return frozenset(r["source_id"] for r in rows if not r.get("supports", "").startswith(INVENTORY_ONLY))


def is_urgent(rule: Obj, row: Obj, family: Obj) -> bool:
    texts: list[str] = []
    for field in _list(rule.get("fields"), "urgent_when fields"):
        owner, _, name = _str(field, "urgent_when field").partition(".")
        value = (row if owner == "row" else family).get(name)
        texts.append(value if isinstance(value, str) else "")
    words = [re.escape(_str(w, "urgent_when word")) for w in _list(rule.get("words"), "urgent_when words")]
    return re.search(r"\b(" + "|".join(words) + r")\b", " ".join(texts), re.IGNORECASE) is not None


def confirming_tests(row: Obj) -> str | None:
    """The kit-map row's confirming test names (E27, from the row's NASA CliFF), in row order, verbatim.

    None when the row has none or when the names would fail the hub's C11 check 3 text rules (a lab test named
    glucose reads as a medicine word): the step then keeps only its lead sentence and cites nothing."""
    names = [
        _str(_obj(t, "confirming test").get("name"), "test name")
        for t in _list(row.get("confirming_tests"), "tests")
    ]
    joined = "; ".join(names)
    if not names or find_amounts(joined) or medicine_words(joined):
        return None
    return joined


def _fill(template: str, family: Obj, where: str, row: Obj | None = None) -> str:
    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in ROW_FIELDS and row is not None:
            tests = confirming_tests(row)
            if tests is None:
                raise BuildError(f"{where}: no usable confirming tests")
            return tests
        if name not in FAMILY_FIELDS:
            raise BuildError(f"{where}: unknown placeholder {name}")
        return _str(family.get(name), f"{where}: family {name}").rstrip(".")

    return PLACEHOLDER.sub(value, template)


def _family_step(raw: Obj, prefix: str, family: Obj, sources_here: list[str], row: Obj, where: str) -> Obj:
    key = _str(raw.get("key"), where)
    if "{confirming_tests}" in _str(raw.get("text"), where) and confirming_tests(row) is None:
        raw = {**raw, "text": _str(raw.get("text_without_tests"), where), "sources": "none"}
    kind = raw.get("sources")
    row_sources = sorted(str(x) for x in _list(row.get("source_ids"), f"{where} source_ids"))
    sources = sources_here if kind == "family" else row_sources if kind == "row" else []
    cannot = raw.get("cannot_replace_text")
    return {
        "step_id": f"{prefix}_{key}",
        "text": _fill(_str(raw.get("text"), where), family, where, row),
        "text_first_time": None,
        "who": _str(raw.get("who"), where),
        "needs": {"instruments": [], "supplies": [], "skills": []},
        "substitute_step_id": None,
        "substitute_only": False,
        "environments": None,
        "body_region": None,
        "landmark_id": None,
        "guided_check_skill_id": None,
        "cannot_replace_text": _fill(cannot, family, where) if isinstance(cannot, str) else None,
        "recheck": raw.get("recheck"),
        "opens_help": raw.get("opens_help") is True,
        "opens_flow_id": None,
        "medicine": None,
        "replaces_ground_task": None,
        "source_ids": sources,
        "basis": _str(raw.get("basis"), where),
        "review_state": "unreviewed",
    }


def expand_family(
    template: Obj, row: Obj, family: Obj, checked_by: str | None, clinical: frozenset[str]
) -> Obj:
    """`row` is the kit-map row merged with its condition-assessments-119 row (name, clinical_assessment,
    limitations)."""
    condition_id = _str(row.get("condition_id"), "kit-map row")
    prefix = condition_id.lower()
    stages: list[object] = []
    sources: set[str] = set()
    variants = _obj(template.get("variants"), "template variants")
    variant = (
        "urgent" if is_urgent(_obj(template.get("urgent_when"), "urgent_when"), row, family) else "standard"
    )
    here = family_sources(family, clinical)
    for raw_stage in _list(variants.get(variant), f"template variant {variant}"):
        stage = _obj(raw_stage, "template stage")
        if stage.get("stage_id") == "ground_log":
            stages.append(stage)
            continue
        steps = [
            _family_step(_obj(s, condition_id), prefix, family, here, row, condition_id)
            for s in _list(stage.get("steps"), condition_id)
        ]
        for step in steps:
            sources.update(_list(step["source_ids"], condition_id))  # type: ignore[arg-type]
        stages.append({"stage_id": stage.get("stage_id"), "steps": steps})
    return {
        "plan_id": plan_id_for(condition_id),
        "title": _str(row.get("name"), condition_id),
        "scope": {"condition_id": condition_id},
        "written_for": "family",
        "mode": _str(template.get("mode"), "template mode"),
        "environments": _list(template.get("environments"), "template environments"),
        "stages": stages,
        "source_ids": sorted(str(s) for s in sources),
        "review_state": "unreviewed",
        "written_by": _str(template.get("written_by"), "template written_by"),
        "checked_by": checked_by,
    }


def checked_sha256(plan: Obj) -> str:
    """SHA-256 of the plan as the checker saw it: canonical JSON without `checked_by`. A verdict counts only
    for the exact content it judged, so any later edit drops `checked_by` until the plan is checked again."""
    body = {key: value for key, value in plan.items() if key != "checked_by"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verdict(check: Obj, plan: Obj) -> str | None:
    entries = _obj(check.get("plans", {}), "check.json plans")
    entry = entries.get(str(plan["plan_id"]))
    if not isinstance(entry, dict) or entry.get("verdict") != AGREES:
        return None
    if entry.get("plan_sha256") != checked_sha256(plan):
        return None
    return _str(check.get("checked_by"), "check.json checked_by")


def flow_plans(check: Obj) -> list[Obj]:
    """authoring/plans/flows/plan_<flow_id>.json in config/responder-flows.json order.

    A flow plan file with no flow fails the build; a flow without a file is reported by check.py."""
    flows = _obj(_load(ROOT / "config/responder-flows.json"), "responder-flows.json")
    order = [_str(_obj(f, "flow").get("id"), "flow id") for f in _list(flows.get("flows"), "flows")]
    files = {p.stem: _obj(_load(p), p.name) for p in sorted((HERE / "flows").glob("plan_*.json"))}
    unknown = sorted(set(files) - {"plan_" + flow_id for flow_id in order})
    if unknown:
        raise BuildError(f"flow plans with no responder flow: {unknown}")
    result: list[Obj] = []
    for flow_id in order:
        plan = files.get("plan_" + flow_id)
        if plan is None:
            continue
        plan = dict(plan)
        plan["checked_by"] = None
        plan["checked_by"] = _verdict(check, plan)
        result.append(plan)
    return result


def build() -> tuple[str, str]:
    doc = _obj(_load(HERE / "doc.json"), "doc.json")
    template = _obj(_load(HERE / "family-template.json"), "family-template.json")
    kit_map = _obj(_load(ROOT / "config/kit-map.json"), "kit-map.json")
    families = _obj(_load(ROOT / "docs/product/assessment-families.json"), "assessment-families.json")
    rows_119 = _list(
        _load(ROOT / "docs/product/condition-assessments-119.json"), "condition-assessments-119.json"
    )
    assessment = {str(_obj(r, "row")["condition_id"]): _obj(r, "row") for r in rows_119}
    clinical = clinical_sources()
    check_path = HERE / "adjudication/check.json"
    check = _obj(_load(check_path), "check.json") if check_path.exists() else {}
    written = {p.stem: _obj(_load(p), p.name) for p in sorted((HERE / "conditions").glob("plan_*.json"))}

    plans: list[Obj] = []
    for raw_row in _list(kit_map.get("conditions"), "kit-map conditions"):
        row = _obj(raw_row, "kit-map row")
        condition_id = _str(row.get("condition_id"), "kit-map row")
        plan_id = plan_id_for(condition_id)
        if plan_id in written:
            plan = dict(written.pop(plan_id))
            plan["checked_by"] = None
            plan["checked_by"] = _verdict(check, plan) if plan.get("written_by") else None
            plans.append(plan)
            continue
        package_row = _obj(assessment.get(condition_id), f"{condition_id} assessment row")
        family_id = _str(package_row.get("family"), f"{condition_id} family")
        family = _obj(families.get(family_id), f"family {family_id}")
        merged = {**package_row, **row}
        plan = expand_family(template, merged, family, None, clinical)
        plan["checked_by"] = _verdict(check, plan)
        plans.append(plan)
    if written:
        raise BuildError(f"written plans with no kit-map row: {sorted(written)}")
    plans.extend(flow_plans(check))

    document = {
        "version": doc["version"],
        "example": doc["example"],
        "notice": doc["notice"],
        "sources": doc.get("sources", []),
        "plans": plans,
    }
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    lines = ["plan_id,written_by,checked_by"]
    lines += [f"{p['plan_id']},{p.get('written_by') or ''},{p.get('checked_by') or ''}" for p in plans]
    return text, "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        text, assignments = build()
    except BuildError as error:
        print(f"build failed: {error}", file=sys.stderr)
        return 1
    outputs = ((OUTPUT, text), (ASSIGNMENTS, assignments))
    if args.check:
        stale = [
            str(path.relative_to(ROOT))
            for path, body in outputs
            if not path.exists() or path.read_text(encoding="utf-8") != body
        ]
        if stale:
            print(f"outputs differ from build.py: {stale}", file=sys.stderr)
            return 1
        return 0
    for path, body in outputs:
        path.write_text(body, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
