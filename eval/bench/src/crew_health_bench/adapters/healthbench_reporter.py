from collections.abc import Mapping

import numpy as np
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.adapters.healthbench import OfficialGrader
from crew_health_bench.application.ports import ArtifactStore
from crew_health_bench.application.publication import ArtifactValidator, publishable_artifacts
from crew_health_bench.domain.documents import integer, number, object_map
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.healthbench import OfficialGradeResult
from crew_health_bench.domain.metrics import bootstrap_interval
from crew_health_bench.domain.runs import Generation, Grade


class HealthBenchReporter:
    def __init__(
        self,
        *,
        official: OfficialGrader,
        store: ArtifactStore,
        cases: tuple[str, ...],
        N: int,
        case_salt: bytes,
        canaries: tuple[str, ...],
        policy: Mapping[str, object],
        plan: Mapping[str, object],
        validator: ArtifactValidator,
    ) -> None:
        self.official, self.store, self.cases, self.N = official, store, cases, N
        self.salt, self.canaries, self.policy, self.plan, self.validator = (
            case_salt,
            canaries,
            policy,
            plan,
            validator,
        )

    def _official(self, run_id: str, grades: tuple[Grade, ...]) -> dict[str, object]:
        archived = {
            (row["case_id"], row["repeat"]): row for row in self.store.read_valid(run_id, "official_grades")
        }
        results: list[OfficialGradeResult] = []
        negative = 0
        for grade in grades:
            if grade.official_score is None:
                continue
            row = archived.get((grade.case_id, grade.repeat))
            if row is None or number(row.get("official_score")) != grade.official_score:
                raise BenchError("integrity", exit_code=6)
            metrics = {
                key: Quantity(number(value), Unit("1")) for key, value in object_map(row["metrics"]).items()
            }
            items = row.get("rubric_items")
            if not isinstance(items, list):
                raise BenchError("integrity", exit_code=6)
            if any(
                number(object_map(item)["points"]) < 0 and object_map(item).get("criteria_met") is True
                for item in items
            ):
                negative += 1
            results.append(
                OfficialGradeResult(grade.case_id, Quantity(grade.official_score, Unit("1")), metrics, "", ())
            )
        report: dict[str, object] = {
            "score": None,
            "metrics": {},
            "our_interval": None,
            "negative_criteria_met": negative,
            "graded": len(results),
            "not_graded": len(grades) - len(results),
        }
        if results:
            seed = int(str(object_map(self.plan["sample"])["seed"]))
            np.random.seed(seed)
            aggregated = self.official.aggregate(tuple(results))
            report.update(
                score=float(aggregated["score"].value),
                metrics={key: float(value.value) for key, value in aggregated.items() if key != "score"},
                our_interval=bootstrap_interval(
                    tuple(float(item.score.value) for item in results),
                    seed=seed,
                    bootstrap_b=integer(self.plan["bootstrap_b"]),
                    interval_level=float(number(self.plan["interval_level"])),
                ),
            )
        return report

    def build(
        self,
        run_id: str,
        manifest: Mapping[str, object],
        generations: tuple[Generation, ...],
        grades: tuple[Grade, ...],
    ) -> Mapping[str, bytes]:
        total = self.N * integer(manifest["repeats"])
        planned = len(self.cases) * integer(manifest["repeats"])
        completeness = (
            "partial" if len(generations) < planned else "full" if len(generations) == total else "subset"
        )
        return publishable_artifacts(
            evaluation_manifest=manifest,
            run_id=run_id,
            generations=generations,
            grades=grades,
            case_conditions=dict.fromkeys(self.cases, ()),
            check_categories={},
            completeness=completeness,
            N=total,
            case_salt=self.salt,
            canaries=self.canaries,
            policy=self.policy,
            validator=self.validator,
            official=self._official(run_id, grades),
        )
