"""Routine evaluation boundaries preserve producer delivery on typed computation faults."""

from collections.abc import Callable

from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.clock import ClockCorrected, ClockRegressed
from crew_health.kernel.evaluation import EvaluatorComputationFailure
from crew_health.kernel.uow import StoredEvent, UnitOfWork
from crew_health.routine.application.audit import audit
from crew_health.routine.application.deps import RoutineDeps
from crew_health.routine.application.engine import RoutineEngine
from crew_health.routine.application.ports import AnswerAccepted
from crew_health.routine.domain.bindings import TriggerInput
from crew_health.routine.domain.errors import RoutineError


class RoutineSubscriptions:
    def __init__(self, deps: RoutineDeps, engine: RoutineEngine) -> None:
        self.deps, self.engine = deps, engine

    def isolated(self, uow: UnitOfWork, work: Callable[[], None]) -> None:
        try:
            with uow.savepoint("routine_input"):
                work()
        except EvaluatorComputationFailure:
            self.deps.health.mark_degraded("routine", "EVALUATION_UNAVAILABLE", uow.now)
            audit(self.deps, uow, "evaluation_failed")
        except RoutineError:
            audit(self.deps, uow, "input_rejected")

    def input(self, uow: UnitOfWork, inp: TriggerInput) -> None:
        self.isolated(uow, lambda: self.engine.apply_input(uow, inp))

    def release(self, uow: UnitOfWork, event: StoredEvent) -> None:
        if event.type is EventType.CONFIG_RELEASE_ACTIVATED:
            self.isolated(uow, lambda: self.engine.release_activated(uow))

    def answer(self, uow: UnitOfWork, event: AnswerAccepted) -> None:
        self.isolated(uow, lambda: self.engine.on_answer(uow, event))

    def regressed(self, uow: UnitOfWork, event: ClockRegressed) -> None:
        self.isolated(uow, lambda: self.engine.on_clock_regressed(uow, event))

    def corrected(self, uow: UnitOfWork, event: ClockCorrected) -> None:
        self.isolated(uow, lambda: self.engine.on_clock_corrected(uow, event))
