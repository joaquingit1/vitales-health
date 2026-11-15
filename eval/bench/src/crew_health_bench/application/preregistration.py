from pathlib import Path

from crew_health.kernel.commands import canonical_json

from crew_health_bench.application.partitioning import EvaluationOwner
from crew_health_bench.application.ports import ArtifactStore
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.preregistration import PreRegistration


class PreRegister:
    def __init__(self, store: ArtifactStore, owner: EvaluationOwner) -> None:
        self.store, self.owner = store, owner

    def freeze(self, registration: PreRegistration, *, generated_plan_hashes: tuple[str, ...]) -> None:
        self.owner.require_owner()
        if registration.plan_sha256 in generated_plan_hashes:
            raise BenchError("already_generated")
        record = registration.record()
        existing = self.store.read_valid(registration.plan_sha256, "preregistration")
        if existing:
            if len(existing) != 1 or canonical_json(existing[0]) != canonical_json(record):
                raise BenchError("preregistration_conflict")
        else:
            self.store.append(registration.plan_sha256, "preregistration", record)
        self.store.write_atomic(Path("preregistration.json"), canonical_json(record))
