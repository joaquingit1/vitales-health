from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory

from crew_health_bench.domain.documents import integer, object_map, text
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.sample import run_id

MANIFEST_FIELDS = (
    "suite",
    "track",
    "partition",
    "variant",
    "case_set_sha256",
    "dataset_sha256",
    "harness",
    "model_release",
    "prompt_sha256",
    "tool_catalog_sha256",
    "evidence_release_id",
    "evidence_sha256",
    "registry_version",
    "config_release_id",
    "bench_documents_sha256",
    "grader",
    "decoding",
    "seed",
    "preregistration_sha256",
    "hardware",
    "software",
    "integrity",
    "repeats",
)


def validate_manifest(manifest: Mapping[str, object], cases: tuple[str, ...]) -> None:
    if set(manifest) != set(MANIFEST_FIELDS) or not cases or len(set(cases)) != len(cases):
        raise BenchError("case_invalid")
    if any(not item for item in cases) or integer(manifest["repeats"]) < 1:
        raise BenchError("case_invalid")
    if manifest["track"] not in ("model_only", "system_assisted", "rules_only"):
        raise BenchError("case_invalid")
    if manifest["partition"] not in ("dev", "holdout", "benchmark"):
        raise BenchError("case_invalid")
    if manifest["integrity"] not in ("official", "modified"):
        raise BenchError("case_invalid")
    if manifest["case_set_sha256"] != sha256(canonical_json(cases)).hexdigest():
        raise BenchError("case_set_mismatch")
    for field in (
        "dataset_sha256",
        "harness",
        "model_release",
        "bench_documents_sha256",
        "grader",
        "decoding",
        "hardware",
        "software",
    ):
        object_map(manifest[field])
    for field in (
        "suite",
        "prompt_sha256",
        "tool_catalog_sha256",
        "evidence_release_id",
        "evidence_sha256",
        "registry_version",
        "config_release_id",
        "seed",
        "preregistration_sha256",
    ):
        if not text(manifest[field]):
            raise BenchError("case_invalid")


def identity(manifest: Mapping[str, object], cases: tuple[str, ...], ids: IdFactory) -> str:
    validate_manifest(manifest, cases)
    return run_id(manifest, ids)
