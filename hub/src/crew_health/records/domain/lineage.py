from __future__ import annotations

from crew_health.kernel.errors import ErrorCode
from crew_health.records.domain.answers import AnswerRecord
from crew_health.records.domain.errors import RecordError
from crew_health.records.domain.observations import ObservationRecord


def check_observation_lineage(record: ObservationRecord, previous: ObservationRecord | None) -> None:
    if record.supersedes is None:
        return
    if (
        previous is None
        or (record.patient_id, record.mission_id, record.origin_mode, record.code)
        != (previous.patient_id, previous.mission_id, previous.origin_mode, previous.code)
        or record.supersedes != previous.observation_id
    ):
        raise RecordError(ErrorCode.IDENTITY_MISMATCH)


def check_answer_lineage(record: AnswerRecord, previous: AnswerRecord | None) -> None:
    if record.supersedes is None:
        return
    if (
        previous is None
        or (record.patient_id, record.mission_id, record.origin_mode, record.question_id)
        != (previous.patient_id, previous.mission_id, previous.origin_mode, previous.question_id)
        or record.supersedes != previous.answer_id
    ):
        raise RecordError(ErrorCode.IDENTITY_MISMATCH)
