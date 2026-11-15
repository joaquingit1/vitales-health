"""Build config/kit-map.json from the package sources and the two-model adjudication (E27 5.3, Q7, Q26).

Deterministic: the same inputs give the same bytes. Inputs, all already in the repository:

- docs/product/condition-assessments-119.json: the 119 condition IDs and their order;
- docs/product/cliff/cliff-confirming-tests.json: per condition the form URL, SHA-256, title, candidate
  tests with verbatim quotes and pages, and body region candidates mapped by Q26;
- docs/product/cliff/forms/ICL<n>.json: saved form text, used to verify every extra quote on its page;
- docs/product/sources.csv: the source registry (S13, NASA's Integrated Medical Model documentation);
- contracts/api/examples/kit-map.example.json: the instrument catalog, capabilities, kits and candidates;
- authoring/kitmap/kit.json: the head (label, notice, defaults for added instruments, the plan ID rule and the
  supplies other release documents reference);
- authoring/kitmap/adjudication/draft.json (drafting model) and check.json (checking model).

A row whose check is not `agrees`, or that the drafter marked undecided, keeps `checked_by: null`
(unchecked) and is listed in the provenance file; nothing is invented to fill it. Conditions whose ID the
contract pattern cannot carry are left out and listed under `omitted_rows`. Every row stays `unreviewed`
(Q1, Q7).

Usage: python authoring/kitmap/build.py [--check]   (--check fails when the outputs on disk differ)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "config/kit-map.json"
PROVENANCE = ROOT / "authoring/kitmap/provenance.json"
ENVIRONMENTS = ("station_leo", "transit", "lunar_surface", "mars_surface")
CAMERA_ESTIMATE = "camera_estimate"
# Coordinator decision D22: condition IDs may carry one capital suffix (ICL36A, ICL36B). The package
# schemas still say ^ICL[1-9][0-9]{0,2}$ until the coordinator lands that contract change.
CONDITION_ID = re.compile(r"^ICL[1-9][0-9]{0,2}[A-Z]?$")
OMITTED_REASON = "condition_id outside the D22 pattern ^ICL[1-9][0-9]{0,2}[A-Z]?$"

type Obj = dict[str, object]


class BuildError(Exception):
    pass


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _obj(value: object) -> Obj:
    if not isinstance(value, dict):
        raise BuildError("object expected")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise BuildError("list expected")
    return value


def _objs(value: object) -> list[Obj]:
    return [_obj(item) for item in _list(value)]


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise BuildError("string expected")
    return value


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _quote_on_page(condition_id: str, quote: str, page: object) -> bool:
    form = ROOT / f"docs/product/cliff/forms/{condition_id}.json"
    if not form.is_file():
        return False
    texts = [_str(p["text"]) for p in _objs(_obj(_load(form))["pages"]) if p["page"] == page]
    return any(_norm(quote) in _norm(t) for t in texts)


def _name(candidate: Obj, assessment: Obj) -> str:
    title = candidate.get("cliff_title") or ""
    prefix = _str(candidate["condition_id"]) + "_"
    if candidate.get("status") == "found" and isinstance(title, str) and title.startswith(prefix):
        return title[len(prefix) :].strip()
    return _str(assessment["condition"]).strip()


def _regions(candidate: Obj, q26: dict[str, list[object]]) -> list[str]:
    """Enum candidates of each form region, then the Q26 mapping of that region (kit.json q26_region_map)."""
    regions: list[str] = []
    for item in _objs(candidate.get("body_region", [])):
        mapped = q26.get(_str(item["region"]).lower(), [])
        for region in [*_list(item.get("enum_candidates", [])), *mapped]:
            if _str(region) not in regions:
                regions.append(_str(region))
    return regions


def _merges(head: Obj) -> dict[str, str]:
    """Instrument IDs the checking model found duplicated: the `from` ID is dropped, rows use `to`."""
    return {_str(m["from"]): _str(m["to"]) for m in _objs(head.get("instrument_merges", []))}


def _catalog(head: Obj, draft: Obj) -> tuple[list[Obj], list[Obj]]:
    example = _obj(_load(ROOT / _str(head["catalog_from"])))
    instruments = [dict(i) for i in _objs(example["instruments"])]
    capabilities = [dict(c) for c in _objs(example["capabilities"])]
    known = {_str(i["id"]) for i in instruments}
    defaults = _obj(head["added_instrument_capability"])
    merges = _merges(head)
    for added in _objs(draft["instruments_added"]):
        added_id, name = _str(added["id"]), _str(added["name"])
        if added_id in merges:
            continue
        if added_id in known:
            raise BuildError("added instrument already in the catalog: " + added_id)
        known.add(added_id)
        instruments.append(
            {"id": added_id, "name": name, "needs_text": added["needs_text"], "capability_codes": [added_id]}
        )
        capabilities.append(
            {
                "code": added_id,
                "label": name[:1].upper() + name[1:],
                "body_region": defaults["body_region"],
                "operator_requirement": defaults["operator_requirement"],
                "expected_duration_seconds": defaults["expected_duration_seconds"],
                "basis": defaults["basis"],
            }
        )
    return instruments, capabilities


def _cited(test: Obj, cands: list[Obj]) -> Obj:
    return {
        "name": test["name"],
        "candidates": [
            {"index": i, "test": cands[i]["test"], "quote": cands[i]["quote"], "page": cands[i]["page"]}
            for i in (_str_or_int(c) for c in _list(test["candidates"]))
        ],
    }


def _str_or_int(value: object) -> int:
    if not isinstance(value, int):
        raise BuildError("candidate index expected")
    return value


def _tests(
    row: Obj, candidate: Obj, methods: dict[str, str], merges: dict[str, str]
) -> tuple[list[Obj], list[Obj]]:
    condition_id = _str(row["condition_id"])
    cands = _objs(candidate["confirming_tests"])
    extras = _objs(row.get("extra_quotes", []))
    for extra in extras:
        if not _quote_on_page(condition_id, _str(extra["quote"]), extra["page"]):
            raise BuildError(f"{condition_id}: extra quote not on page {extra['page']}")
    tests: list[Obj] = []
    cited: list[Obj] = []
    for test in _objs(row["tests"]):
        indexes = [_str_or_int(c) for c in _list(test["candidates"])]
        if not indexes and not extras:
            raise BuildError(f"{condition_id}: test cites nothing: {test['name']}")
        if any(not 0 <= index < len(cands) for index in indexes):
            raise BuildError(f"{condition_id}: candidate out of range")
        names = list(dict.fromkeys(merges.get(_str(i), _str(i)) for i in _list(test["instruments"])))
        for instrument in names:
            if instrument not in methods:
                raise BuildError(f"{condition_id}: unknown instrument {instrument}")
            if methods[instrument] == CAMERA_ESTIMATE:
                raise BuildError(f"{condition_id}: a camera estimate never confirms a condition")
        tests.append({"name": test["name"], "instruments": names})
        cited.append(_cited(test, cands))
    return tests, cited


def _inputs() -> tuple[Obj, list[Obj], dict[str, Obj], dict[str, Obj], dict[str, Obj], Obj, Obj]:
    head = _obj(_load(ROOT / "authoring/kitmap/kit.json"))
    assessments = _objs(_load(ROOT / "docs/product/condition-assessments-119.json"))
    forms = _objs(_load(ROOT / "docs/product/cliff/cliff-confirming-tests.json"))
    draft = _obj(_load(ROOT / _str(head["drafts"])))
    check = _obj(_load(ROOT / _str(head["checks"])))
    with (ROOT / "docs/product/sources.csv").open(encoding="utf-8", newline="") as handle:
        registry = {r["source_id"] for r in csv.DictReader(handle)}
    for source in _list(head["source_ids"]):
        if source not in registry:
            raise BuildError("source not in docs/product/sources.csv: " + _str(source))
    order = [_str(a["condition_id"]) for a in assessments]
    drafted = {_str(r["condition_id"]): r for r in _objs(draft["rows"])}
    verdicts = {_str(r["condition_id"]): r for r in _objs(check["rows"])}
    complete = len(order) == len(set(order)) == 119
    if not complete or set(drafted) != set(order) or set(verdicts) != set(order):
        raise BuildError("expected exactly the 119 condition IDs in every input")
    candidates = {_str(c["condition_id"]): c for c in forms}
    return head, assessments, candidates, drafted, verdicts, draft, check


def _provenance(cid: str, candidate: Obj, row: Obj, verdict: Obj, cited: list[Obj], named: bool) -> Obj:
    return {
        "condition_id": cid,
        "form": {"url": candidate["url"], "sha256": candidate["sha256"], "status": candidate["status"]},
        "name_from": "cliff_title" if named else "condition-assessments-119.json",
        "body_region_quotes": candidate.get("body_region", []),
        "tests": cited,
        "extra_quotes": row.get("extra_quotes", []),
        "excluded": row.get("excluded", []),
        "why": row.get("why", ""),
        "undecided": bool(row.get("undecided", False)),
        "check": {"verdict": verdict["verdict"], "reason": verdict.get("reason", "")},
        "checked": verdict["verdict"] == "agrees" and not row.get("undecided", False),
    }


def _supplies(head: Obj, example: Obj) -> list[Obj]:
    """The supplies kit.json names, in its order, copied from the example's catalog; an unknown ID fails."""
    catalog = {_str(s["supply_id"]): s for s in _objs(example["supplies"])}
    wanted = [_str(i) for i in _list(_obj(head["supplies"])["ids"])]
    missing = [i for i in wanted if i not in catalog]
    if missing:
        raise BuildError("supplies not in the example catalog: " + ", ".join(missing))
    return [dict(catalog[i]) for i in wanted]


def build() -> tuple[bytes, bytes]:
    head, assessments, candidates, drafted, verdicts, draft, check = _inputs()
    instruments, capabilities = _catalog(head, draft)
    methods = {_str(i["id"]): _str(i.get("method", "manual_instrument")) for i in instruments}
    example = _obj(_load(ROOT / _str(head["catalog_from"])))
    q26 = {key: _list(value) for key, value in _obj(_obj(head["q26_region_map"])["regions"]).items()}
    plan_prefix = _str(_obj(head["plan_ids"])["prefix"])
    rows: list[Obj] = []
    provenance: list[Obj] = []
    omitted: list[Obj] = []
    for assessment in assessments:
        cid = _str(assessment["condition_id"])
        candidate, row, verdict = candidates[cid], drafted[cid], verdicts[cid]
        tests, cited = _tests(row, candidate, methods, _merges(head))
        if not CONDITION_ID.fullmatch(cid):
            omitted.append({"condition_id": cid, "reason": OMITTED_REASON})
            continue
        name = _name(candidate, assessment)
        side = _provenance(cid, candidate, row, verdict, cited, name != _str(assessment["condition"]).strip())
        rows.append(
            {
                "condition_id": cid,
                "name": name,
                "body_regions": _regions(candidate, q26),
                "confirming_tests": tests,
                "cliff_url": candidate["url"],
                "plan_id": plan_prefix + cid.lower(),
                "ground_needs": None,
                "treatment_scope": None,
                "rtdc": None,
                "source_ids": list(_list(head["source_ids"])),
                "review_state": "unreviewed",
                "written_by": draft["drafted_by"],
                "checked_by": check["checked_by"] if side["checked"] else None,
            }
        )
        provenance.append(side)
    kits = _obj(example["kits"])
    supplies = _supplies(head, example)
    document = {
        "version": head["version"],
        "example": head["example"],
        "notice": head["notice"],
        "instruments": instruments,
        "capabilities": capabilities,
        "kits": {e: list(_list(kits[e])) for e in ENVIRONMENTS},
        "candidates": list(_list(example["candidates"])),
        "candidate_supplies": [],
        "candidate_skills": [],
        "supplies": supplies,
        "stock": {e: [] for e in ENVIRONMENTS},
        "conditions": rows,
    }
    side_file = {
        "drafted_by": draft["drafted_by"],
        "checked_by": check["checked_by"],
        "instrument_checks": check["instruments"],
        "instrument_merges": head.get("instrument_merges", []),
        "unchecked_rows": [p["condition_id"] for p in provenance if not p["checked"]],
        "omitted_rows": omitted,
        "rows": provenance,
    }
    return _dump(document), _dump(side_file)


def _dump(value: object) -> bytes:
    return (json.dumps(value, indent=1, ensure_ascii=True) + "\n").encode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        document, side = build()
    except (BuildError, KeyError, ValueError) as error:
        sys.exit(f"kit map build failed: {error}")
    if args.check:
        if OUTPUT.read_bytes() != document or PROVENANCE.read_bytes() != side:
            sys.exit("config/kit-map.json or provenance.json differs from build.py output")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(document)
    PROVENANCE.write_bytes(side)


if __name__ == "__main__":
    main()
