"""Records invokes this evaluator inside the SYSTEM 8.6 platform savepoint."""

from crew_health.kernel.evaluation import EvaluationInput, Evaluator
from crew_health.kernel.uow import UnitOfWork
from crew_health.routine.application.audit import audit
from crew_health.routine.application.deps import RoutineDeps
from crew_health.routine.application.engine import RoutineEngine
from crew_health.routine.domain.admission import admissible
from crew_health.routine.domain.bindings import ObservationInput
from crew_health.routine.domain.errors import MissionUnavailable, RoutineError


class RoutineEvaluator(Evaluator):
    def __init__(self, deps: RoutineDeps, engine: RoutineEngine) -> None:
        self.deps, self.engine = deps, engine

    def evaluate(self, uow: UnitOfWork, value: EvaluationInput) -> None:
        store = self.deps.records
        if store is None:
            self.deps.health.mark_degraded("routine.seam.records", "CAPABILITY_UNAVAILABLE", uow.now)
            return
        try:
            answer = store.answer(uow, value.input_ref)
            if answer is not None:
                self.engine.on_answer(uow, answer, answer.mission_id)
                return
            record = store.observation(uow, value.input_ref)
            if record is None:
                return
            mission = self.engine.mission(uow)
            if record.mission_id != mission.mission_id:
                audit(self.deps, uow, "foreign_mission", "observation")
                return
            if not admissible(
                mission_mode=mission.mission_mode,
                input_origin=record.origin_mode,
                group_audience=False,
                on_roster=None
                if self.deps.roster is None
                else record.patient_id in self.deps.roster.person_ids(),
            ).ok:
                audit(self.deps, uow, "origin_rejected", "observation")
                return
            self.engine.apply_input(
                uow,
                ObservationInput(
                    record.observation_id,
                    record.patient_id,
                    record.code,
                    record.quantity.value,
                    record.quantity.unit.code,
                    record.normalized.corrected_at or record.observed.original_at,
                    record.origin_mode,
                    record.quality_status,
                ),
            )
        except MissionUnavailable:
            audit(self.deps, uow, "mission_unknown")
        except RoutineError:
            audit(self.deps, uow, "input_rejected")
