from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

from crew_health.kernel.commands import canonical_json

from crew_health_bench.domain.cases import Case, Partition
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.partition import PartitionPolicy, assign_groups, group_cases
from crew_health_bench.domain.text import fingerprints


def _texts(cases: tuple[Case, ...], texts: Mapping[str, tuple[str, ...]]) -> None:
    if not cases or texts.keys() != {case.case_id for case in cases}:
        raise BenchError("case_invalid")
    for case in cases:
        parts = texts[case.case_id]
        if (len(parts) != (3 if case.revealable is not None else 2) or case.visible not in parts
                or not all(case.canary in part for part in parts)
                or not any(sha256(part.encode("utf-8")).hexdigest() == case.hidden_sha256 for part in parts)):
            raise BenchError("case_invalid")


def _stratification(cases: tuple[Case, ...], assignments: Mapping[str, Partition], groups: int) -> dict[str, object]:
    families: dict[str, dict[str, int]] = {}
    suites: dict[str, dict[str, int]] = {}
    for case in sorted(cases, key=lambda item: item.case_id):
        for inventory, name in ((families, case.key.family_id), (suites, case.suite.value)):
            row = inventory.setdefault(name, {partition.value: 0 for partition in Partition})
            row[assignments[case.case_id].value] += 1
    return {"families": families, "suites": suites, "groups": groups,
            "not_in_dev": sorted(name for name, row in families.items() if row[Partition.DEV.value] == 0)}


def partition_artifacts(
    cases: tuple[Case, ...], policy_document: Mapping[str, object], *, salt: bytes, fp_salt: str,
    texts: Mapping[str, tuple[str, ...]], previous: Mapping[str, Partition],
) -> dict[str, bytes]:
    if not fp_salt:
        raise BenchError("salt_missing")
    _texts(cases, texts)
    policy = PartitionPolicy.from_document(policy_document)
    groups = group_cases(cases, policy)
    assignments = assign_groups(groups, policy, salt=salt, previous=previous)
    rows = [{"case_id": case.case_id, "group_id": group_id, "partition": assignments[case.case_id].value,
             "suite": case.suite.value, "family_id": case.key.family_id}
            for group_id, members in sorted(groups.items()) for case in members]
    manifest = {"case_set_sha256": sha256(canonical_json(sorted(texts))).hexdigest(),
                "policy_sha256": sha256(canonical_json(dict(policy_document))).hexdigest(), "partitions": rows}
    sealed = tuple(case for case in cases if assignments[case.case_id] != Partition.DEV)
    fp_rows = [{"case_ref": sha256(salt + case.case_id.encode("utf-8")).hexdigest(),
                "shingles": sorted(fingerprints(part, fp_salt=fp_salt)), "canaries": [case.canary]}
               for case in sorted(sealed, key=lambda item: item.case_id) for part in texts[case.case_id]]
    return {
        "partitions/manifest.json": canonical_json(manifest),
        "partitions/canaries.json": canonical_json({"canaries": sorted(case.canary for case in sealed)}),
        "fingerprints/holdout.fp.json": canonical_json({"fp_salt": fp_salt, "cases": fp_rows}),
        "partitions/stratification.json": canonical_json(_stratification(cases, assignments, len(groups))),
    }
