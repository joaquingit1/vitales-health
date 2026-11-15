"""finish (E30 3.4, 3.5, 4.3). R reads the check and its revision and pre-checks the capture; the camera outcome is
read outside any unit of work; T reloads, requires the revision read in R, evaluates the agreement on exact values,
writes the Observation for every complete result (OD1), saves, audits, emits guided_check.checked and publishes
GuidedCheckCompleted. The hub never fabricates a camera value: no reading, a different origin, an incommensurable
unit or a pending tolerance all end as no_reference."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import UnitOfWork
from crew_health.teach.application import access
from crew_health.teach.application.actions import CAMERA_ERRORS, OPERATION, origin
from crew_health.teach.application.commands import CommandMeta, Response, TeachCore
from crew_health.teach.application.content import PHRASE_FIELD, PinnedContent
from crew_health.teach.application.errors import problem_of
from crew_health.teach.application.ports import GuidedCheckCompleted, ObservationDraft, ReferenceOutcome
from crew_health.teach.domain.agreement import (
    AgreementOutcome,
    comparable_origins,
    display_decimals,
    evaluate_count_agreement,
    rounded,
)
from crew_health.teach.domain.capture import ClockReading
from crew_health.teach.domain.catalog import CountAgreementCheck, SkillDefinition
from crew_health.teach.domain.errors import IncompleteCapture, TeachError
from crew_health.teach.domain.guided_check import CameraResult, GuidedCheck

QUALITY_RULE_VERSION = "guided-check-1"  # version of the capture and agreement rules in this code (3.5)
# The camera's refusal when its source (LIVE, SIM, REPLAY) is not the count's (camera/application/reference.py): 3.6
# never compares them, and the screen says so instead of a bare "not available here".
ORIGIN_MISMATCH = "camera_origin_mismatch"


@dataclass(frozen=True, slots=True)
class _Read:
    revision: int
    ticket_id: str | None
    unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class _Reference:
    camera: CameraResult
    comparable: Fraction | None
    reason: str | None


class FinishGuidedCheck(TeachCore):
    def __call__(self, meta: CommandMeta, guided_check_id: str, if_match: str | None) -> Response:
        identity = self.identity(meta, OPERATION, guided_check_id)
        with self.unit(meta, None) as view:
            replay = self.replay(view, identity, meta.session)
            if replay is not None:
                return replay
            read = self._read(view, meta, guided_check_id, if_match)
        if isinstance(read, ProblemError):
            raise read  # after the commit that recorded the incomplete attempt (3.3)
        outcome = self._outcome(read.ticket_id)
        failure: ProblemError | None = None
        with self.unit(meta, identity) as uow:
            replay = self.replay(uow, identity, meta.session)
            if replay is not None:
                return replay
            check = self.load(uow, guided_check_id)
            access.authorize(meta.session, check, "act")
            if check.revision != read.revision:
                current = self.response(check, meta.session, 200).body
                raise ProblemError(
                    ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value, current_state=current
                )
            r = self.reading()
            content = self.content_of(check)
            skill = content.skill(check.skill_id)
            count = self._prepare(uow, meta, check, content, r)
            if isinstance(count, ProblemError):
                failure = count
            else:
                self._finish(uow, meta, check, content, skill, count, outcome, read, r)
                self.remember(uow, identity, check, 200)
                return self.response(check, meta.session, 200, r)
        raise failure

    def _read(
        self, view: UnitOfWork, meta: CommandMeta, guided_check_id: str, if_match: str | None
    ) -> _Read | ProblemError:
        check = self.load(view, guided_check_id)
        access.authorize(meta.session, check, "act")
        self.if_match(check, if_match, meta.session)
        prepared = self._prepare(view, meta, check, self.content_of(check), self.reading())
        if isinstance(prepared, ProblemError):
            return prepared
        attempt = check.attempt
        ticket = attempt.camera_ticket_id if attempt is not None else None
        reason = attempt.camera_unavailable_reason if attempt is not None else None
        return _Read(check.revision, ticket, reason)

    def _prepare(
        self, uow: UnitOfWork, meta: CommandMeta, check: GuidedCheck, content: PinnedContent, r: ClockReading
    ) -> Fraction | ProblemError:
        """The exact count of a complete capture, or the 422 to raise after this unit of work commits the attempt
        as incomplete with its reasons (3.3): never a rate, never an Observation, finish no longer offered."""
        skill = content.skill(check.skill_id)
        try:
            return check.prepare_finish(skill, r, max_gap_seconds=self.p.settings.count_max_gap_seconds)
        except IncompleteCapture as error:
            check.mark_incomplete(error.reasons)
            self.save(uow, check)
            self.audit(uow, meta.session, "guided_check_incomplete", check)
            # guided_check_words.count_again is absent from the package ui-copy: null until it lands (3.3)
            word = content.guided_check_word("count_again")
            mapped = problem_of(error)
            return ProblemError(
                mapped.code,
                mapped.code.value,
                field_errors=mapped.field_errors,
                supported_alternative=None if word is None else {"action": "cancel", "text": word},
            )
        except TeachError as error:
            raise self.refuse(uow, check.guided_check_id, meta.session, error) from error

    def _outcome(self, ticket_id: str | None) -> ReferenceOutcome | None:
        camera = self.p.camera
        if camera is None or ticket_id is None:
            return None
        try:
            return camera.outcome(ticket_id, wait=self.ms(self.p.settings.reference_wait_ms))
        except CAMERA_ERRORS:
            return None

    def _finish(
        self,
        uow: UnitOfWork,
        meta: CommandMeta,
        check: GuidedCheck,
        content: PinnedContent,
        skill: SkillDefinition,
        count: Fraction,
        outcome: ReferenceOutcome | None,
        read: _Read,
        r: ClockReading,
    ) -> None:
        count_check = skill.count_check
        if count_check is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        reference = self._reference(check, count_check, outcome, read)
        tolerance = count_check.tolerance.value if count_check.tolerance is not None else None
        agreement = evaluate_count_agreement(count, reference.comparable, tolerance)
        decimals = content.decimals(count_check.reference_code)
        reason = _reason(content, reference, tolerance)
        shown = display_decimals(count, reference.comparable, tolerance, agreement.result, decimals)
        text = self._text(uow, count_check, agreement, count, reference, reason, shown)
        observation_id = self._observe(uow, check, skill, count_check, count, agreement, decimals, r)
        camera = reference.camera
        if camera.value is None:
            camera = CameraResult(camera.observation_id, None, None, reason, camera.origin_mode)
        check.apply_finish(
            result=agreement.result,
            difference=agreement.difference,
            count=count,
            camera=camera,
            result_text=text,
            observation_id=observation_id,
            r=r,
        )
        self.save(uow, check)
        self.audit(uow, meta.session, "guided_check_finished", check)
        body = self.response(check, meta.session, 200, r).body
        body["allowed_actions"] = check.allowed_actions(skill, r)
        self.emit(uow, check, EventType.GUIDED_CHECK_CHECKED, body, r.utc)
        written = (observation_id,) if check.measured_record_id == check.subject_id else ()
        uow.publish(
            GuidedCheckCompleted(
                check.guided_check_id, check.plan_run_id, check.plan_step_id, agreement.result, written
            )
        )

    def _reference(
        self,
        check: GuidedCheck,
        count_check: CountAgreementCheck,
        outcome: ReferenceOutcome | None,
        read: _Read,
    ) -> _Reference:
        """The camera reference as shown, and the exact value compared, only from a real, comparable reading."""
        count_origin = origin(check)
        if outcome is None or outcome.kind != "reading" or outcome.value is None:
            # a window the camera refused to open is the hub's refusal code, not the camera's words: only a refusal
            # for another source is said as such; any other one reads "not available here" (3.4 step 3)
            refused = ORIGIN_MISMATCH if read.unavailable_reason == ORIGIN_MISMATCH else None
            reason = outcome.reason if outcome is not None and outcome.reason else refused
            camera = CameraResult(
                None, None, None, reason, (outcome.origin_mode if outcome else None) or count_origin
            )
            return _Reference(camera, None, reason)
        if not comparable_origins(count_origin, outcome.origin_mode):
            camera = CameraResult(None, None, None, None, outcome.origin_mode or count_origin)
            return _Reference(camera, None, ORIGIN_MISMATCH)
        if not self._fresh(outcome):
            camera = CameraResult(None, None, None, None, outcome.origin_mode or count_origin)
            return _Reference(camera, None, None)
        canonical = self.p.observations.canonical_unit(count_check.reference_code)
        value: Quantity = outcome.value
        if value.unit != canonical:
            if not self.p.units.commensurable(value.unit, canonical):
                camera = CameraResult(None, None, None, None, outcome.origin_mode or count_origin)
                return _Reference(camera, None, None)
            value = self.p.units.convert(value, canonical)
        exact = Fraction(value.value)
        camera = CameraResult(
            outcome.observation_id, exact, canonical.code, None, outcome.origin_mode or count_origin
        )
        return _Reference(camera, exact, None)

    def _fresh(self, outcome: ReferenceOutcome) -> bool:
        uncertainty = outcome.clock_uncertainty_ms
        return uncertainty is not None and uncertainty <= self.p.settings.camera_clock_tolerance_ms

    def _text(
        self,
        uow: UnitOfWork,
        count_check: CountAgreementCheck,
        agreement: AgreementOutcome,
        count: Fraction,
        reference: _Reference,
        reason: str,
        decimals: int,
    ) -> str:
        phrase = count_check.phrases.get(agreement.phrase_key)
        if not phrase:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=False)
        values = {"count": str(rounded(count, decimals)), "reason": reason}
        if reference.comparable is not None:
            values["camera"] = str(rounded(reference.comparable, decimals))
        if agreement.difference is not None:
            values["difference"] = str(rounded(abs(agreement.difference), decimals))
        return self.p.config.bind(uow).render(PHRASE_FIELD, phrase, values)

    def _observe(
        self,
        uow: UnitOfWork,
        check: GuidedCheck,
        skill: SkillDefinition,
        count_check: CountAgreementCheck,
        count: Fraction,
        agreement: AgreementOutcome,
        decimals: int,
        r: ClockReading,
    ) -> str:
        attempt = check.attempt
        step = check.count_step(skill)
        if attempt is None or step.count_seconds is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        unit = self.p.observations.canonical_unit(count_check.reference_code)
        draft = ObservationDraft(
            patient_id=check.measured_record_id,
            operator_id=check.helper_id,
            episode_id=check.episode_id if check.measured_record_id == check.subject_id else None,
            code=count_check.reference_code,
            value=Quantity(rounded(count, decimals), unit),
            observed_at=attempt.window.ends_utc(),
            entered_at=r.utc,
            origin_mode=origin(check),
            acquisition_method="crewmate_palpation",
            quality_status="accepted",
            quality_rule_version=QUALITY_RULE_VERSION,
            body_region=step.body_region,
            context={
                "guided_check_id": check.guided_check_id,
                "count_seconds": str(step.count_seconds),
                "camera_agreement": agreement.result,
            },
        )
        key = f"{check.guided_check_id}:{attempt.window.attempt_no}:obs"
        return self.p.observations.append_observation(uow, draft, idempotency_key=key)


def _reason(content: PinnedContent, reference: _Reference, tolerance: Fraction | None) -> str:
    """3.4 step 3: the camera's words, else not available here, else (tolerance pending) threshold pending. A camera
    on another source than the count (3.6) reads as not compared for that reason, when the pinned ui-copy has it."""
    if reference.reason == ORIGIN_MISMATCH:
        word = content.guided_check_word("not_compared_origin")
        return word if word is not None else content.designed_state("not_available_here")
    if reference.reason:
        return reference.reason
    if reference.comparable is not None and tolerance is None:
        return content.designed_state("threshold_pending")
    return content.designed_state("not_available_here")
