from __future__ import annotations

from collections.abc import Sequence

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.ids import require_id
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.records.application.ports import RecordAuthorization, RecordsStore
from crew_health.records.domain.answers import AnswerRecord
from crew_health.records.domain.errors import RecordError
from crew_health.records.domain.observations import ObservationRecord, OriginMode


class RecordsQueries:
    def __init__(
        self, repository: RecordsStore, authorization: RecordAuthorization, settings: HubSettings
    ) -> None:
        self.repository, self.authorization, self.settings = repository, authorization, settings

    def observation(
        self, view: ReadSnapshot | UnitOfWork, session: SessionContext, observation_id: str
    ) -> ObservationRecord:
        require_id(observation_id)
        result = self.repository.observation(view, observation_id)
        if result is None:
            raise RecordError(ErrorCode.NOT_FOUND)
        self.authorization.require_read(
            view,
            session,
            object_ref=result.observation_id,
            patient_id=result.patient_id,
            fields=frozenset({"observation"}),
        )
        return result

    def accepted(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        person_id: str,
        code: str,
        *,
        mission_id: str,
        origin_mode: OriginMode,
        limit: int,
    ) -> tuple[ObservationRecord, ...]:
        self._bounds(person_id, mission_id, origin_mode, limit)
        self.authorization.require_read(
            view, session, object_ref=person_id, patient_id=person_id, fields=frozenset({"observation"})
        )
        results = self.repository.observations(
            view, person_id=person_id, mission_id=mission_id, origin_mode=origin_mode, code=code, limit=limit
        )
        for result in results:
            self.authorization.require_read(
                view,
                session,
                object_ref=result.observation_id,
                patient_id=result.patient_id,
                fields=frozenset({"observation"}),
            )
        return tuple(result for result in results if result.normalized.state in ("known", "replay"))

    def answers(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        person_id: str,
        question_id: str,
        *,
        mission_id: str,
        origin_mode: OriginMode,
        limit: int,
    ) -> tuple[AnswerRecord, ...]:
        self._bounds(person_id, mission_id, origin_mode, limit)
        self.authorization.require_read(
            view, session, object_ref=person_id, patient_id=person_id, fields=frozenset({"answer"})
        )
        results = self.repository.answers(
            view,
            person_id=person_id,
            mission_id=mission_id,
            origin_mode=origin_mode,
            question_id=question_id,
            limit=limit,
        )
        for result in results:
            self.authorization.require_read(
                view,
                session,
                object_ref=result.answer_id,
                patient_id=result.patient_id,
                fields=frozenset({"answer"}),
            )
        return results

    def get_many(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        observation_ids: Sequence[str],
        *,
        person_id: str,
        mission_id: str,
        origin_mode: OriginMode,
    ) -> dict[str, ObservationRecord]:
        self._bounds(person_id, mission_id, origin_mode, max(1, len(observation_ids)))
        if len(observation_ids) > self.settings.integer("records.query_max_ids"):
            raise RecordError(ErrorCode.INVALID_VALUE)
        results: dict[str, ObservationRecord] = {}
        for observation_id in sorted(set(observation_ids)):
            result = self.observation(view, session, observation_id)
            if (result.patient_id, result.mission_id, result.origin_mode) != (
                person_id,
                mission_id,
                origin_mode,
            ):
                raise RecordError(ErrorCode.IDENTITY_MISMATCH)
            results[observation_id] = result
        return results

    def _bounds(self, person_id: str, mission_id: str, origin_mode: OriginMode, limit: int) -> None:
        require_id(person_id)
        require_id(mission_id)
        if (
            origin_mode not in ("LIVE", "REPLAY", "SIM")
            or type(limit) is not int
            or not 1
            <= limit
            <= min(self.settings.integer("records.query_max_rows"), self.settings.integer("storage.max_rows"))
        ):
            raise RecordError(ErrorCode.INVALID_VALUE)
