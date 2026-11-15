from __future__ import annotations

from dataclasses import dataclass

from crew_health.kernel.evaluation import EvaluationInput, EvaluationIsolation
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.uow import UnitOfWork
from crew_health.records.domain.answers import AnswerAccepted
from crew_health.records.domain.observations import ObservationRecord


@dataclass(frozen=True, slots=True)
class RecordEvaluation:
    isolation: EvaluationIsolation
    ids: IdFactory
    evaluator: str
    evaluator_version: str

    def answer_accepted(self, uow: UnitOfWork, event: AnswerAccepted) -> None:
        answer = event.answer
        self.isolation.run(
            uow,
            EvaluationInput(
                self.evaluator,
                self.evaluator_version,
                answer.answer_id,
                answer.observed,
                answer.release_id,
                self.ids.derived("partition", answer.patient_id, answer.mission_id, answer.origin_mode),
                1,
                self.ids.derived("completion", self.evaluator, self.evaluator_version, answer.answer_id),
            ),
        )

    def observation_accepted(self, uow: UnitOfWork, observation: ObservationRecord) -> None:
        if observation.quality_status != "accepted" or observation.normalized.state not in (
            "known",
            "replay",
        ):
            return
        self.isolation.run(
            uow,
            EvaluationInput(
                self.evaluator,
                self.evaluator_version,
                observation.observation_id,
                observation.observed,
                observation.release_id,
                self.ids.derived(
                    "partition", observation.patient_id, observation.mission_id, observation.origin_mode
                ),
                1,
                self.ids.derived(
                    "completion", self.evaluator, self.evaluator_version, observation.observation_id
                ),
            ),
        )
