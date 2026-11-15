from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from crew_health_bench.domain.cases import Case, Partition, Suite, validate_cases
from crew_health_bench.domain.documents import integer, number, object_map, text_list
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.text import jaccard, shingles


@dataclass(frozen=True, slots=True)
class PartitionPolicy:
    group_by: tuple[str, ...]
    permutations: int
    bands: int
    seed: int
    near_duplicate_jaccard: Decimal
    holdout_fraction: Decimal
    min_groups_for_split: int

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> PartitionPolicy:
        minhash = object_map(document["minhash"])
        return cls(
            text_list(document["group_by"]),
            integer(minhash["permutations"]),
            integer(minhash["bands"]),
            integer(minhash["seed"]),
            number(document["near_duplicate_jaccard"]),
            number(document["holdout_fraction"]),
            integer(document["min_groups_for_split"]),
        )

    def __post_init__(self) -> None:
        if len(self.group_by) != 4 or set(self.group_by) != {
            "family_id",
            "source_vignette_id",
            "patient_id",
            "episode_id",
        }:
            raise BenchError("case_invalid")
        if self.permutations < 1 or self.bands < 1 or self.permutations % self.bands:
            raise BenchError("case_invalid")
        if not 0 < self.near_duplicate_jaccard <= 1 or not 0 < self.holdout_fraction < 1:
            raise BenchError("case_invalid")
        if self.min_groups_for_split < 1:
            raise BenchError("case_invalid")


def _key(case: Case, name: str) -> str:
    return {
        "family_id": case.key.family_id,
        "source_vignette_id": case.key.source_vignette_id,
        "patient_id": case.key.patient_id,
        "episode_id": case.key.episode_id,
    }[name]


def _root(parents: dict[str, str], item: str) -> str:
    for _ in range(len(parents)):
        if parents[item] == item:
            return item
        item = parents[item]
    raise BenchError("case_invalid")


def _union(parents: dict[str, str], a: str, b: str) -> None:
    left, right = _root(parents, a), _root(parents, b)
    parents[max(left, right)] = min(left, right)


def _near_pairs(cases: tuple[Case, ...], policy: PartitionPolicy) -> tuple[tuple[str, str], ...]:
    texts = {case.case_id: shingles(case.visible, 5) for case in cases}
    buckets: dict[tuple[int, tuple[bytes, ...]], list[str]] = defaultdict(list)
    candidates: set[tuple[str, str]] = set()
    width = policy.permutations // policy.bands
    for case in cases:
        grams = texts[case.case_id]
        if not grams:
            continue
        signature = tuple(
            min(sha256(f"{policy.seed}:{index}:{gram}".encode()).digest() for gram in sorted(grams))
            for index in range(policy.permutations)
        )
        for band in range(policy.bands):
            key = (band, signature[band * width : (band + 1) * width])
            for other in buckets[key]:
                candidates.add((other, case.case_id))
            buckets[key].append(case.case_id)
    return tuple(
        (a, b)
        for a, b in sorted(candidates)
        if Decimal(str(jaccard(texts[a], texts[b]))) >= policy.near_duplicate_jaccard
    )


def group_cases(cases: tuple[Case, ...], policy: PartitionPolicy) -> dict[str, tuple[Case, ...]]:
    validate_cases(cases)
    ordered = tuple(sorted(cases, key=lambda case: case.case_id))
    parents = {case.case_id: case.case_id for case in ordered}
    shared: dict[tuple[str, str], str] = {}
    for case in ordered:
        for name in policy.group_by:
            key = (name, _key(case, name))
            if key in shared:
                _union(parents, case.case_id, shared[key])
            shared[key] = case.case_id
        if case.derived_from is not None:
            _union(parents, case.case_id, case.derived_from)
    for left, right in _near_pairs(ordered, policy):
        _union(parents, left, right)
    groups: dict[str, list[Case]] = defaultdict(list)
    for case in ordered:
        groups[_root(parents, case.case_id)].append(case)
    return {root: tuple(members) for root, members in sorted(groups.items())}


def partition(
    cases: tuple[Case, ...],
    policy: PartitionPolicy,
    *,
    salt: bytes,
    previous: Mapping[str, Partition],
) -> dict[str, Partition]:
    if len(salt) != 32:
        raise BenchError("salt_missing")
    groups = group_cases(cases, policy)
    return assign_groups(groups, policy, salt=salt, previous=previous)


def assign_groups(
    groups: Mapping[str, tuple[Case, ...]], policy: PartitionPolicy, *, salt: bytes,
    previous: Mapping[str, Partition],
) -> dict[str, Partition]:
    if len(salt) != 32:
        raise BenchError("salt_missing")
    result: dict[str, Partition] = {}
    for root, members in sorted(groups.items()):
        exposed = {previous[case.case_id] for case in members if case.case_id in previous}
        external = {case.suite == Suite.HEALTHBENCH for case in members}
        if len(exposed) > 1 or len(external) > 1:
            raise BenchError("group_spans_partitions")
        if exposed:
            assignment = next(iter(exposed))
            if (assignment == Partition.BENCHMARK) != (external == {True}):
                raise BenchError("group_spans_partitions")
        elif external == {True}:
            assignment = Partition.BENCHMARK
        else:
            bucket = int(sha256(salt + root.encode()).hexdigest()[:8], 16) % 10000
            assignment = (
                Partition.HOLDOUT
                if (len(groups) < policy.min_groups_for_split or bucket < policy.holdout_fraction * 10000)
                else Partition.DEV
            )
        for case in members:
            result[case.case_id] = assignment
    return result
