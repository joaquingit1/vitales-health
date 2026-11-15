"""Routine's RecordFacts over records' real store (B2): the composition adapter that reaches the records an answer or
observation evaluation names in create_app, without a change to records.

Records registers its store under `records.application.seams.RecordsStore`, a module that imports records' domain,
so routine may not import it (the generated `routine-independent-records` contract). Records' cross-module port
`ObservationCommands` (records.application.ports, domain free) is provided by its ObservationProducer, which holds
that same store. module.py resolves that provider and this file checks its store against the read half routine
needs. Nothing here reads or writes a record itself: every call is the records store's own answer. When records
registers a provider under routine's RecordFacts, module.py resolves it instead and this file goes."""

from __future__ import annotations

from typing import Protocol, cast, runtime_checkable

from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.routine.application.ports import AnswerFacts, ObservationFacts, RecordFacts


@runtime_checkable
class RecordReads(Protocol):
    def observation(self, view: ReadSnapshot | UnitOfWork, observation_id: str) -> object | None: ...
    def answer(self, view: ReadSnapshot | UnitOfWork, answer_id: str) -> object | None: ...


@runtime_checkable
class RecordsProducer(Protocol):
    repository: object


class SharedRecordFacts:
    def __init__(self, store: RecordReads) -> None:
        self.store = store

    def observation(self, view: ReadSnapshot | UnitOfWork, observation_id: str) -> ObservationFacts | None:
        return cast(ObservationFacts | None, self.store.observation(view, observation_id))

    def answer(self, view: ReadSnapshot | UnitOfWork, answer_id: str) -> AnswerFacts | None:
        return cast(AnswerFacts | None, self.store.answer(view, answer_id))


def shared_record_facts(producer: object) -> RecordFacts | None:
    if not isinstance(producer, RecordsProducer) or not isinstance(producer.repository, RecordReads):
        return None
    return SharedRecordFacts(producer.repository)
