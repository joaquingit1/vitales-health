from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from crew_health_bench.application.ports import AtomicArtifacts
from crew_health_bench.domain.cases import Case, Partition
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.partition_artifacts import partition_artifacts


class EvaluationOwner(Protocol):
    def require_owner(self) -> None: ...


class FreezePartitions:
    def __init__(self, *, store: AtomicArtifacts, owner: EvaluationOwner) -> None:
        self.store, self.owner = store, owner

    def freeze(self, cases: tuple[Case, ...], policy: Mapping[str, object], *, salt: bytes, fp_salt: str,
               texts: Mapping[str, tuple[str, ...]], previous: Mapping[str, Partition]) -> None:
        self.owner.require_owner()
        try:
            files = partition_artifacts(cases, policy, salt=salt, fp_salt=fp_salt, texts=texts, previous=previous)
        except (ValueError, UnicodeError) as error:
            raise BenchError("case_invalid") from error
        for name, data in sorted(files.items()):
            self.store.write_atomic(Path(name), data)
        marker = "".join(sha256(data).hexdigest() + "  " + name + "\n" for name, data in sorted(files.items()))
        self.store.write_atomic(Path("partitions/FROZEN.sha256"), marker.encode("utf-8"))
