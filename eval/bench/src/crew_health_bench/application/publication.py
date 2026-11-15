from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from hashlib import sha256
from typing import Protocol

from crew_health.kernel.commands import canonical_json

from crew_health_bench.domain.claims import ClaimsPolicy, lint_claims
from crew_health_bench.domain.documents import integer, object_map, text
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.manifest import validate_manifest
from crew_health_bench.domain.metrics import category_metrics
from crew_health_bench.domain.results import benchmark_labels, scenario_outcome, status
from crew_health_bench.domain.runs import Generation, Grade


class ArtifactValidator(Protocol):
    def validate(self, name: str, document: object) -> None: ...


def _case_ref(case_id: str, salt: bytes) -> str:
    if len(salt) != 32:
        raise BenchError("salt_missing")
    return sha256(salt + case_id.encode("utf-8")).hexdigest()


def _rows(
    generations: tuple[Generation, ...],
    grades: tuple[Grade, ...],
    *,
    case_conditions: Mapping[str, tuple[str, ...]],
    case_salt: bytes,
    completeness: str,
    integrity: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    by_key = {(item.case_id, item.repeat): item for item in grades}
    keys = {(item.case_id, item.repeat) for item in generations}
    if len(by_key) != len(grades) or len(keys) != len(generations) or not by_key.keys() <= keys:
        raise BenchError("case_invalid")
    rows: list[dict[str, object]] = []
    scenarios: list[dict[str, object]] = []
    for item in generations:
        if item.case_id not in case_conditions:
            raise BenchError("case_invalid")
        ref = _case_ref(item.case_id, case_salt)
        grade = by_key.get((item.case_id, item.repeat))
        outcomes = grade.outcomes if grade is not None else ()
        rows.append(
            {
                "case_ref": ref,
                "repeat": item.repeat,
                "status": item.status,
                "outcomes": [{"check": check, "outcome": outcome} for check, outcome, _ in outcomes],
                "official_score": float(grade.official_score)
                if grade and grade.official_score is not None
                else None,
                "wall": item.record("")["wall"],
                "completeness": completeness,
                "integrity": integrity,
            }
        )
        for condition in case_conditions[item.case_id] or (None,):
            scenarios.append(
                {
                    "scenario_id": ref,
                    "condition_id": condition,
                    "outcome": scenario_outcome(item.status, tuple(o for _, o, _ in outcomes)),
                }
            )
    return rows, scenarios


def _missing_rows(
    rows: list[dict[str, object]],
    scenarios: list[dict[str, object]],
    generations: tuple[Generation, ...],
    case_conditions: Mapping[str, tuple[str, ...]],
    *,
    repeats: int,
    salt: bytes,
    completeness: str,
    integrity: str,
) -> None:
    present = {(item.case_id, item.repeat) for item in generations}
    for case_id, conditions in case_conditions.items():
        ref = _case_ref(case_id, salt)
        for repeat in range(repeats):
            if (case_id, repeat) in present:
                continue
            rows.append(
                {
                    "case_ref": ref,
                    "repeat": repeat,
                    "status": "not_run",
                    "outcomes": [],
                    "official_score": None,
                    "wall": None,
                    "completeness": completeness,
                    "integrity": integrity,
                }
            )
            for condition in conditions or (None,):
                scenarios.append({"scenario_id": ref, "condition_id": condition, "outcome": "missing"})


def _categories(
    generations: tuple[Generation, ...],
    grades: tuple[Grade, ...],
    check_categories: Mapping[str, str],
    *,
    planned: int,
) -> dict[str, object]:
    values: dict[tuple[str, int, str], list[str]] = {}
    for grade in grades:
        for check, outcome, _ in grade.outcomes:
            if check not in check_categories:
                raise BenchError("case_invalid")
            values.setdefault((grade.case_id, grade.repeat, check_categories[check]), []).append(outcome)
    categories: dict[str, object] = {}
    for category in sorted(set(check_categories.values())):
        outcomes: list[str] = []
        for generation in generations:
            checks = values.get((generation.case_id, generation.repeat, category))
            if checks is None:
                continue
            if generation.status != "completed" or "not_graded" in checks:
                outcomes.append("not_graded")
            elif "fail" in checks:
                outcomes.append("fail")
            elif "pass" in checks:
                outcomes.append("pass")
            else:
                outcomes.append("not_applicable")
        categories[category] = asdict(category_metrics(tuple(outcomes), n_cases=planned))
    return categories


def _report(aggregate: Mapping[str, object], policy: Mapping[str, object]) -> bytes:
    templates = object_map(policy.get("report_templates"))
    fields = ("track", "variant", "completeness", "integrity", "n", "N", "grader")
    lines = [text(templates.get("qualification")), text(templates.get("simulator"))]
    lines.extend(f"{field}: {aggregate[field]}" for field in fields)
    if "official" in aggregate:
        official = object_map(aggregate["official"])
        lines.extend(
            f"{text(templates.get(field))}: {official[field]}"
            for field in ("score", "metrics", "our_interval", "negative_criteria_met", "graded", "not_graded")
        )
    if aggregate["variant"] is not None:
        lines.append(text(policy.get("benchmark_disclaimer")))
    content = "\n\n".join(lines) + "\n"
    if lint_claims(content, ClaimsPolicy.from_document(policy)):
        raise BenchError("claims_lint_failed")
    return content.encode("utf-8")


def _labels(
    manifest: Mapping[str, object], completeness: str, n: int, N: int, policy: Mapping[str, object]
) -> dict[str, object]:
    track, integrity = text(manifest["track"]), text(manifest["integrity"])
    grader = text(object_map(manifest["grader"])["model"])
    labels: dict[str, object] = {
        "track": track,
        "variant": manifest["variant"],
        "completeness": completeness,
        "integrity": integrity,
        "n": n,
        "N": N,
        "grader": grader,
        "reviewer_qualification": "engineering",
    }
    if manifest["variant"] is not None:
        labels.update(
            benchmark_labels(
                track=track,
                variant=text(manifest["variant"]),
                completeness=completeness,
                integrity=integrity,
                n=n,
                N=N,
                grader=grader,
                benchmark_disclaimer=text(policy.get("benchmark_disclaimer")),
            )
        )
    return labels


def _serialize(
    manifest: Mapping[str, object],
    aggregate: Mapping[str, object],
    rows: list[dict[str, object]],
    *,
    policy: Mapping[str, object],
    validator: ArtifactValidator,
    canaries: tuple[str, ...],
) -> dict[str, bytes]:
    validator.validate("run-manifest", manifest)
    validator.validate("aggregate", aggregate)
    for row in rows:
        validator.validate("case-results", row)
    artifacts = {
        "run_manifest.json": canonical_json(manifest),
        "aggregate.json": canonical_json(aggregate),
        "case_results.jsonl": b"".join(canonical_json(row) + b"\n" for row in rows),
        "REPORT.md": _report(aggregate, policy),
    }
    if any(canary.encode("utf-8") in data for canary in canaries for data in artifacts.values()):
        raise BenchError("privacy_scan_failed")
    artifacts["privacy_scan.json"] = canonical_json({"findings": [], "files": sorted(artifacts)})
    return artifacts


def publishable_artifacts(
    *,
    evaluation_manifest: Mapping[str, object],
    run_id: str,
    generations: tuple[Generation, ...],
    grades: tuple[Grade, ...],
    case_conditions: Mapping[str, tuple[str, ...]],
    check_categories: Mapping[str, str],
    completeness: str,
    N: int,
    case_salt: bytes,
    canaries: tuple[str, ...],
    policy: Mapping[str, object],
    validator: ArtifactValidator,
    official: Mapping[str, object] | None = None,
) -> dict[str, bytes]:
    planned = len(case_conditions) * integer(evaluation_manifest.get("repeats"))
    if (
        not generations
        or N < len(generations)
        or len(generations) > planned
        or (completeness == "full" and len(generations) != planned)
        or not canaries
        or any(not item for item in canaries)
    ):
        raise BenchError("case_invalid")
    validate_manifest(evaluation_manifest, tuple(case_conditions))
    integrity = text(evaluation_manifest["integrity"])
    rows, scenarios = _rows(
        generations,
        grades,
        case_conditions=case_conditions,
        case_salt=case_salt,
        completeness=completeness,
        integrity=integrity,
    )
    _missing_rows(
        rows,
        scenarios,
        generations,
        case_conditions,
        repeats=integer(evaluation_manifest["repeats"]),
        salt=case_salt,
        completeness=completeness,
        integrity=integrity,
    )
    labels = _labels(evaluation_manifest, completeness, len(generations), N, policy)
    aggregate = {
        **labels,
        "categories": _categories(generations, grades, check_categories, planned=planned),
        "statuses": {
            s: sum(row["status"] == s for row in rows) for s in sorted({text(row["status"]) for row in rows})
        },
    }
    if official is not None:
        aggregate["official"] = dict(official)
    manifest = {
        **evaluation_manifest,
        **labels,
        "grader": evaluation_manifest["grader"],
        "run_id": run_id,
        "release_id": text(object_map(evaluation_manifest["model_release"])["release_id"]),
        "state": "reported",
        "status": status("reported", completeness),
        "model_pin": evaluation_manifest["model_release"],
        "harness_version": text(object_map(evaluation_manifest["harness"])["commit"]),
        "scenarios": scenarios,
    }
    return _serialize(manifest, aggregate, rows, policy=policy, validator=validator, canaries=canaries)
