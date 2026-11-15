"""T-E18-10/15/16/28/30: allowlisted, labeled artifacts preserve missing cases."""

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.units import Quantity, Unit
from crew_health_bench.adapters.artifacts import ArtifactSchemas
from crew_health_bench.adapters.documents import load_document
from crew_health_bench.application.publication import publishable_artifacts
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.runs import Generation, Grade

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "R30", "T-E18-15", "T-E18-16", "T-E18-28")]
ROOT = Path(__file__).parents[1]


def evaluation_manifest(
    track: str = "system_assisted", variant: str | None = None, integrity: str = "modified"
) -> dict[str, object]:
    return {
        "suite": "tool",
        "track": track,
        "partition": "dev",
        "variant": variant,
        "case_set_sha256": sha256(canonical_json(("first", "second"))).hexdigest(),
        "dataset_sha256": {},
        "harness": {"commit": "pinned"},
        "model_release": {"release_id": "release"},
        "prompt_sha256": "a" * 64,
        "tool_catalog_sha256": "b" * 64,
        "evidence_release_id": "evidence",
        "evidence_sha256": "c" * 64,
        "registry_version": "registry",
        "config_release_id": "config",
        "bench_documents_sha256": {},
        "grader": {"model": "deterministic"},
        "decoding": {},
        "seed": "1",
        "preregistration_sha256": "none",
        "hardware": {},
        "software": {},
        "integrity": integrity,
        "repeats": 1,
    }


def records() -> tuple[Generation, ...]:
    at = datetime(2026, 11, 14, tzinfo=UTC)
    return tuple(
        Generation(
            case,
            0,
            state,
            at,
            at,
            "epoch",
            Quantity(Decimal(1), Unit("s")),
            "a" * 64,
            "private/output",
            "private/trace",
            0,
        )
        for case, state in (("first", "completed"), ("second", "timeout"))
    )


def test_public_rows_have_salted_refs_and_no_private_trace(tmp_path: Path) -> None:
    policy = load_document(ROOT / "config/claims-policy.json", max_file_bytes=16777216)
    artifacts = publishable_artifacts(
        evaluation_manifest=evaluation_manifest(),
        run_id="run",
        generations=records(),
        grades=(Grade("first", 0, (("NoFabricatedResult", "pass", ("private evidence",)),), None, 0, 0),),
        case_conditions={"first": ("ICL10", "ICL11"), "second": ()},
        check_categories={"NoFabricatedResult": "grounding"},
        completeness="partial",
        N=2,
        case_salt=b"x" * 32,
        canaries=("journal_secret",),
        policy=policy,
        validator=ArtifactSchemas(ROOT / "schemas", 16777216),
    )
    import json

    manifest = json.loads(artifacts["run_manifest.json"])
    assert manifest["status"] == "partial"
    assert manifest["reviewer_qualification"] == "engineering"
    assert len(manifest["scenarios"]) == 3
    aggregate = json.loads(artifacts["aggregate.json"])
    assert aggregate["categories"]["grounding"]["n_cases"] == 2
    assert aggregate["categories"]["grounding"]["missing"] == 1
    assert aggregate["statuses"] == {"completed": 1, "timeout": 1}
    combined = b"".join(artifacts.values())
    for private in (b"private evidence", b"private/output", b"private/trace", b'"first"', b"journal_secret"):
        assert private not in combined
    assert b"partial" in artifacts["REPORT.md"] and b"modified" in artifacts["REPORT.md"]


def test_publication_refuses_secret_and_ungraded_claim() -> None:
    policy = load_document(ROOT / "config/claims-policy.json", max_file_bytes=16777216)
    arguments = dict(
        evaluation_manifest=evaluation_manifest("model_only", "main", "official"),
        run_id="journal_secret",
        generations=records(),
        grades=(),
        case_conditions={"first": (), "second": ()},
        check_categories={},
        completeness="full",
        N=2,
        case_salt=b"x" * 32,
        canaries=("journal_secret",),
        policy=policy,
        validator=ArtifactSchemas(ROOT / "schemas", 16777216),
    )
    with pytest.raises(BenchError, match="privacy_scan_failed"):
        publishable_artifacts(**arguments)


def arguments() -> dict[str, object]:
    return dict(
        evaluation_manifest=evaluation_manifest(),
        run_id="run",
        generations=records(),
        grades=(),
        case_conditions={"first": (), "second": ()},
        check_categories={"check": "grounding"},
        completeness="partial",
        N=2,
        case_salt=b"x" * 32,
        canaries=("journal_secret",),
        policy=load_document(ROOT / "config/claims-policy.json", max_file_bytes=16777216),
        validator=ArtifactSchemas(ROOT / "schemas", 16777216),
    )


def test_partial_run_keeps_unrun_case_and_unmeasured_duration() -> None:
    import json

    options = {
        **arguments(),
        "generations": records()[:1],
        "grades": (Grade("first", 0, (("check", "pass", ()),), None, 0, 0),),
    }
    artifacts = publishable_artifacts(**options)
    aggregate = json.loads(artifacts["aggregate.json"])
    assert aggregate["categories"]["grounding"]["n_cases"] == 2
    assert aggregate["categories"]["grounding"]["missing"] == 1
    assert aggregate["statuses"] == {"completed": 1, "not_run": 1}
    rows = [json.loads(line) for line in artifacts["case_results.jsonl"].splitlines()]
    assert rows[1]["status"] == "not_run" and rows[1]["wall"] is None
    manifest = json.loads(artifacts["run_manifest.json"])
    assert manifest["grader"] == evaluation_manifest()["grader"]


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("pass", "passed"),
        ("fail", "failed"),
        ("not_applicable", "not_applicable"),
        ("not_graded", "not_graded"),
    ],
)
def test_categories_preserve_each_distinct_outcome(outcome: str, expected: str) -> None:
    import json

    grade = Grade("first", 0, (("check", outcome, ()),), None, 0, 0)
    artifacts = publishable_artifacts(**{**arguments(), "grades": (grade,)})
    category = json.loads(artifacts["aggregate.json"])["categories"]["grounding"]
    assert category[expected] == 1
    assert category["missing"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"generations": ()},
        {"N": 0},
        {"canaries": ()},
        {"canaries": ("",)},
        {"case_salt": b""},
        {"generations": records()[:1], "completeness": "full"},
        {"generations": (records()[0], records()[0])},
        {"grades": (Grade("unknown", 0, (), None, 0, 0),)},
        {"grades": (Grade("first", 0, (("unregistered check", "pass", ()),), None, 0, 0),)},
    ],
)
def test_invalid_publication_fails_before_writing(changes: dict[str, object]) -> None:
    with pytest.raises(BenchError):
        publishable_artifacts(**{**arguments(), **changes})


def test_unknown_case_refuses_instead_of_omitting_it() -> None:
    from dataclasses import replace

    with pytest.raises(BenchError):
        publishable_artifacts(**{**arguments(), "generations": (replace(records()[0], case_id="unknown"),)})


def test_configured_claims_lint_stops_public_report() -> None:
    policy = load_document(ROOT / "config/claims-policy.json", max_file_bytes=16777216)
    policy["report_templates"]["qualification"] = "HealthBench diagnostic accuracy"
    with pytest.raises(BenchError, match="claims_lint_failed"):
        publishable_artifacts(**{**arguments(), "policy": policy})
