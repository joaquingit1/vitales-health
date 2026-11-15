from __future__ import annotations

from dataclasses import replace

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import ClockCorrections
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.units import Quantity, UnitRegistry, WireUnitConverter
from crew_health.kernel.uow import SessionContext, UnitOfWork
from crew_health.records.application.ports import AnswerAcceptedFact
from crew_health.records.application.seams import (
    AnswerWorkflow,
    EpisodeBinding,
    ObservationEvaluation,
    ObservationPolicies,
    ProducerProof,
    RecordAuthorization,
    RecordsStore,
    SubjectBinding,
)
from crew_health.records.domain.answers import AnswerAccepted, AnswerRecord, load_questions, validate_answer
from crew_health.records.domain.errors import RecordError
from crew_health.records.domain.lineage import check_answer_lineage, check_observation_lineage
from crew_health.records.domain.observations import (
    ObservationInput,
    ObservationRecord,
    QualityAssessment,
    RecordContext,
    assess_quality,
)


class RecordsCommands:
    def __init__(
        self,
        *,
        repository: RecordsStore,
        ids: IdFactory,
        units: UnitRegistry,
        converter: WireUnitConverter,
        authorization: RecordAuthorization,
        binding: SubjectBinding,
        policies: ObservationPolicies,
        clocks: ClockCorrections,
        proof: ProducerProof | None = None,
        episodes: EpisodeBinding | None = None,
        workflow: AnswerWorkflow | None = None,
        evaluations: tuple[ObservationEvaluation, ...] = (),
    ) -> None:
        self.repository, self.ids, self.units, self.converter = repository, ids, units, converter
        self.authorization, self.binding, self.policies, self.clocks = (
            authorization,
            binding,
            policies,
            clocks,
        )
        self.proof, self.episodes, self.workflow = proof, episodes, workflow
        self.evaluations = evaluations

    def record_observation(
        self, uow: UnitOfWork, session: SessionContext, value: ObservationInput
    ) -> ObservationRecord:
        context = self.binding.bind(uow, session, value)
        self.authorization.require_write(uow, session, context)
        _partition(context, value)
        policy = self.policies.for_code(uow.release, value.code)
        policy.validate()
        if policy.code != value.code or value.acquisition_method not in policy.methods:
            raise RecordError(ErrorCode.CONTEXT_INAPPLICABLE)
        if value.original.unit not in policy.accepted_wire_units:
            raise RecordError(ErrorCode.INVALID_UNIT)
        conversion = self.converter.convert(
            value.original, indicator_code=value.code, release=uow.release, to=policy.canonical_unit
        )
        if conversion.original != value.original or conversion.canonical.unit != policy.canonical_unit:
            raise RecordError(ErrorCode.INVALID_UNIT)
        proof_ref = self._proof(uow, session, context, value)
        normalized = self.clocks.normalize(uow, context.observed)
        history = self.repository.observations(
            uow,
            person_id=context.patient_id,
            mission_id=context.mission_id,
            origin_mode=context.origin_mode,
            code=value.code,
            limit=policy.stuck_count,
            accepted_only=False,
            before=normalized.corrected_at or value.observed_at,
            source=(value.acquisition_method, value.device_id),
            corrected_only=True,  # in SQL before the LIMIT, so uncorrected rows never shorten the window
        )
        previous = tuple(
            (r.normalized.corrected_at, r.quantity) for r in history if r.normalized.corrected_at is not None
        )
        quality = assess_quality(
            policy, conversion.canonical, normalized.corrected_at or value.observed_at, previous
        )
        if normalized.state in ("unknown", "future"):
            quality = QualityAssessment("uncertain", quality.reasons + ("time_" + normalized.state,))
        record = ObservationRecord(
            self.ids.new("obs"),
            context.patient_id,
            context.operator_id,
            context.mission_id,
            value.episode_id,
            value.code,
            conversion.canonical,
            conversion,
            context.observed,
            normalized,
            uow.now,
            uow.now,
            context.origin_mode,
            value.acquisition_method,
            quality.status,
            policy.version,
            quality.reasons,
            value.device_id,
            value.context,
            value.body_region,
            value.supersedes,
            uow.release.release_id,
            proof_ref,
            value.measurement_request_id,
            context.care_basis_ref,
        )
        self._episode(uow, session, context, record.episode_id)
        check_observation_lineage(
            record, None if record.supersedes is None else self.repository.observation(uow, record.supersedes)
        )
        self.repository.add_observation(uow, record)
        self._audit(uow, record.observation_id, "observation.recorded")
        for evaluator in self.evaluations:
            evaluator.observation_accepted(uow, record)
        return record

    def _proof(
        self, uow: UnitOfWork, session: SessionContext, context: RecordContext, value: ObservationInput
    ) -> str | None:
        if value.acquisition_method in (
            "self_report",
            "manual_instrument",
            "crewmate_palpation",
            "clinician_entry",
        ):
            if session.kind == "adapter" or session.actor.kind == "assistant":
                raise RecordError(ErrorCode.UNAUTHORIZED_SCOPE)
            return None
        if self.proof is None:
            raise RecordError(ErrorCode.ADAPTER_NOT_TRUSTED)
        return require_id(self.proof.verify(uow, session, value, context))

    def _episode(
        self, uow: UnitOfWork, session: SessionContext, context: RecordContext, episode_id: str | None
    ) -> None:
        if episode_id is not None:
            if self.episodes is None:
                raise RecordError(ErrorCode.CONTEXT_INAPPLICABLE)
            self.episodes.require(uow, session, episode_id=episode_id, context=context)

    def append_answer(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        answer: AnswerRecord,
        *,
        release: ReleaseHandle | None = None,
    ) -> AnswerRecord:
        existing = self._existing_answer(uow, session, answer)
        if existing is not None:
            return existing
        pinned = release or uow.release
        self._answer_context(uow, session, answer, pinned)
        questions = load_questions(pinned)
        if answer.question_id not in questions:
            raise RecordError(ErrorCode.INVALID_VALUE)
        question = questions[answer.question_id]
        value = validate_answer(question, presence=answer.presence, value=answer.value, units=self.units)
        if question.kind == "confirm_estimate" and answer.presence == "present":
            self._estimate(uow, answer, question.answer_code)
        normalized = replace(answer, value=value)
        check_answer_lineage(
            normalized, None if answer.supersedes is None else self.repository.answer(uow, answer.supersedes)
        )
        self.repository.add_answer(uow, normalized)
        self._audit(uow, answer.answer_id, "answer.recorded")
        uow.publish(AnswerAccepted(normalized, answer.episode_id, answer.mission_id))
        uow.publish(
            AnswerAcceptedFact(
                normalized.answer_id, normalized.patient_id, normalized.question_id, normalized.presence,
                normalized.answered_at, normalized.origin_mode, normalized.mission_id,
            )
        )  # fmt: skip
        return normalized

    def _existing_answer(
        self, uow: UnitOfWork, session: SessionContext, answer: AnswerRecord
    ) -> AnswerRecord | None:
        existing = self.repository.answer(uow, answer.answer_id)
        if existing is not None:
            self.authorization.require_read(
                uow,
                session,
                object_ref=answer.answer_id,
                patient_id=existing.patient_id,
                fields=frozenset({"answer"}),
            )
            replay_value = answer.value
            if isinstance(existing.value, Quantity) and isinstance(replay_value, Quantity):
                replay_value = self.units.convert(replay_value, existing.value.unit)
            if existing != replace(answer, value=replay_value):
                raise RecordError(ErrorCode.DUPLICATE_COMMAND)
            return existing
        return None

    def _answer_context(
        self, uow: UnitOfWork, session: SessionContext, answer: AnswerRecord, pinned: ReleaseHandle
    ) -> None:
        if answer.release_id != pinned.release_id or answer.questionnaire_version != pinned.version_of(
            ConfigName.QUESTIONNAIRES
        ):
            raise RecordError(ErrorCode.CONFIG_VERSION_MISMATCH)
        if (
            answer.actor != session.actor
            or answer.actor != uow.actor
            or answer.answered_at != answer.observed.original_at
        ):
            raise RecordError(ErrorCode.IDENTITY_MISMATCH)
        if answer.origin_mode != answer.observed.origin_mode:
            raise RecordError(ErrorCode.ORIGIN_MISMATCH)
        if self.workflow is None:
            raise RecordError(ErrorCode.CONTEXT_INAPPLICABLE)
        self.workflow.require(uow, session, answer)

    def _estimate(self, uow: UnitOfWork, answer: AnswerRecord, code: str | None) -> None:
        if answer.estimate_observation_id is None:
            if isinstance(answer.value, Quantity):
                return
            raise RecordError(ErrorCode.CONTEXT_INAPPLICABLE)
        estimate = self.repository.observation(uow, answer.estimate_observation_id)
        if (
            estimate is None
            or (estimate.patient_id, estimate.mission_id, estimate.origin_mode)
            != (answer.patient_id, answer.mission_id, answer.origin_mode)
            or estimate.quality_status != "accepted"
            or code is None
            or estimate.code != code
        ):
            raise RecordError(ErrorCode.IDENTITY_MISMATCH)

    def _audit(self, uow: UnitOfWork, record_id: str, event_type: str) -> None:
        uow.audit(
            AuditEntry(
                self.ids.new("audit"),
                event_type,
                1,
                uow.actor,
                {"record": record_id},
                uow.correlation_id,
                None,
                {},
            )
        )


def _partition(context: RecordContext, value: ObservationInput) -> None:
    for identifier in (context.patient_id, context.operator_id, context.mission_id, context.care_basis_ref):
        require_id(identifier)
    if context.patient_id != value.patient_id or context.observed.original_at != value.observed_at:
        raise RecordError(ErrorCode.IDENTITY_MISMATCH)
    if context.origin_mode != value.origin_mode or context.observed.origin_mode != value.origin_mode:
        raise RecordError(ErrorCode.ORIGIN_MISMATCH)
