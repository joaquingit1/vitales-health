"""ActOnGuidedCheck except finish (E30 4.2, 4.3): step_done, start_count, answer, retry and cancel. start_count and
retry commit T1, then ask the camera for a reference window outside any unit of work (bounded), then T2 stores the
ticket only if the check is still at that attempt. cancel abandons the ticket after its commit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.commands import CommandIdentity
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import UnitOfWork
from crew_health.teach.application import access
from crew_health.teach.application.commands import CommandMeta, Response, TeachCore
from crew_health.teach.application.ports import (
    AnswerDraft,
    OriginMode,
    ReferenceTicket,
    ReferenceWindowRequest,
)
from crew_health.teach.domain.capture import ClockReading
from crew_health.teach.domain.catalog import question_id
from crew_health.teach.domain.errors import InvalidValue, TeachError
from crew_health.teach.domain.guided_check import GuidedCheck, presence

OPERATION = "actOnGuidedCheck"
CAMERA_ERRORS = (ProblemError, TimeoutError, ConnectionError)  # a camera failure is never an error (4.7)


@dataclass(frozen=True, slots=True)
class ActionInput:
    action: str
    client_time: datetime
    step_no: int | None
    answer: str | None


@dataclass(frozen=True, slots=True)
class _Window:
    guided_check_id: str
    attempt_no: int
    request: ReferenceWindowRequest


def origin(check: GuidedCheck) -> OriginMode:
    return "SIM" if check.count_origin == "SIM" else "LIVE"


class ActOnGuidedCheck(TeachCore):
    """Dispatch of the actions; finish has its own use case because it reads the camera between two phases."""

    def __call__(
        self, meta: CommandMeta, guided_check_id: str, data: ActionInput, if_match: str | None
    ) -> Response:
        identity = self.identity(meta, OPERATION, guided_check_id)
        window: _Window | None = None
        with self.unit(meta, identity) as uow:
            replay = self.replay(uow, identity, meta.session)
            if replay is not None:
                window = self._unreferenced(uow, guided_check_id, data.action)
        if replay is not None:
            return replay if window is None else self._reference(meta, window)
        with self.unit(meta, identity) as uow:
            replay = self.replay(uow, identity, meta.session)
            if replay is not None:
                return replay
            check = self.load(uow, guided_check_id)
            access.authorize(meta.session, check, "cancel" if data.action == "cancel" else "act")
            self.if_match(check, if_match, meta.session)
            r = self.reading()
            ticket = check.attempt.camera_ticket_id if check.attempt is not None else None
            try:
                window = self._apply(uow, check, data, r)
            except TeachError as error:
                raise self.refuse(uow, guided_check_id, meta.session, error) from error
            self._commit(uow, meta, identity, check, data.action)
        if data.action == "cancel" and ticket is not None:
            self._abandon(ticket)
        if window is not None:
            return self._reference(meta, window)
        return self.response(check, meta.session, 200)

    def _apply(
        self, uow: UnitOfWork, check: GuidedCheck, data: ActionInput, r: ClockReading
    ) -> _Window | None:
        skill = self.content_of(check).skill(check.skill_id)
        if data.action == "step_done":
            if data.step_no is None:
                raise InvalidValue("step_no_required", fields=("/step_no",))
            check.step_done(skill, data.step_no, r)
        elif data.action == "start_count":
            check.start_count(skill, r, data.client_time)
            return self._window(check)
        elif data.action == "retry":
            check.retry(skill, r, data.client_time, max_attempts=self.p.settings.max_attempts)
            return self._window(check)
        elif data.action == "answer":
            self._answer(uow, check, data, r)
        elif data.action == "cancel":
            check.cancel()
        else:
            raise InvalidValue("action_not_here", fields=("/action",))
        return None

    def _commit(
        self, uow: UnitOfWork, meta: CommandMeta, identity: CommandIdentity, check: GuidedCheck, action: str
    ) -> None:
        self.save(uow, check)
        self.audit(uow, meta.session, "guided_check_" + action, check)
        self.remember(uow, identity, check, 200)

    def _answer(self, uow: UnitOfWork, check: GuidedCheck, data: ActionInput, r: ClockReading) -> None:
        if data.answer is None:
            raise InvalidValue("answer_required", fields=("/answer",))
        content = self.content_of(check)
        step = check.answer(content.skill(check.skill_id), data.answer, r)
        attempt_no = check.attempt.window.attempt_no if check.attempt is not None else 0
        measured_is_subject = check.measured_record_id == check.subject_id
        draft = AnswerDraft(
            record_id=check.measured_record_id,
            question_id=question_id(check.skill_id, step.step_no),
            questionnaire_version=check.pins["skills-catalog"],
            presence=presence(data.answer),
            value=data.answer,
            body_region=step.body_region,
            actor_person_id=check.helper_id,
            answered_at=r.utc,
            origin_mode=origin(check),
            supersedes=check.previous_answer_id,
            episode_id=check.episode_id if measured_is_subject else None,
        )
        key = f"{check.guided_check_id}:{attempt_no}:answer:{step.step_no}"
        check.answer_id = self.p.answers.append_answer(uow, draft, idempotency_key=key)

    def _window(self, check: GuidedCheck) -> _Window:
        attempt = check.attempt
        content = self.content_of(check)
        count_check = content.skill(check.skill_id).count_check
        if attempt is None or count_check is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        window = attempt.window
        ttl = timedelta(seconds=window.count_seconds + self.p.settings.camera_ticket_margin_s)
        request = ReferenceWindowRequest(
            guided_check_id=check.guided_check_id,
            attempt_no=window.attempt_no,
            record_id=check.measured_record_id,
            capability_code=count_check.reference_code,
            window_start=window.started_utc,
            window_end=window.ends_utc(),
            required_origin=origin(check),
            persist=True,
            ticket_ttl=ttl,
        )
        return _Window(check.guided_check_id, window.attempt_no, request)

    def _unreferenced(self, uow: UnitOfWork, guided_check_id: str, action: str) -> _Window | None:
        """A replayed start_count or retry whose T2 never stored a ticket (crash or storage failure after T1) asks
        the camera again, so a lost reply never silently loses the reference (4.3)."""
        if action not in ("start_count", "retry"):
            return None
        check = self.load(uow, guided_check_id)
        attempt = check.attempt
        if check.state != "counting" or attempt is None or attempt.window.ended(self.reading()):
            return None
        if attempt.camera_ticket_id is not None or attempt.camera_unavailable_reason is not None:
            return None
        return self._window(check)

    def _reference(self, meta: CommandMeta, window: _Window) -> Response:
        """T2: the ticket is saved only while the check is still counting at that attempt (4.3). If T2 fails, the
        ticket is abandoned and the error returned; the client's retry with the same key asks the camera again."""
        ticket = self._begin(window.request)
        try:
            with self.unit(meta, None) as uow:
                check = self.load(uow, window.guided_check_id)
                attempt = check.attempt
                current = (
                    check.state == "counting"
                    and attempt is not None
                    and attempt.window.attempt_no == window.attempt_no
                    and attempt.camera_ticket_id is None
                )
                changed = ticket.ticket_id is not None or ticket.unavailable_reason is not None
                if current and changed and attempt is not None:
                    attempt.camera_ticket_id = ticket.ticket_id
                    attempt.camera_unavailable_reason = ticket.unavailable_reason
                    self.save(uow, check)
        except ProblemError:
            if ticket.ticket_id is not None:
                self._abandon(ticket.ticket_id)
            raise
        if not current and ticket.ticket_id is not None:
            self._abandon(ticket.ticket_id)
        return self.response(check, meta.session, 200)

    def _begin(self, request: ReferenceWindowRequest) -> ReferenceTicket:
        camera = self.p.camera
        if camera is None:
            return ReferenceTicket(None, None)
        try:
            return camera.begin_window(request, timeout=self.ms(self.p.settings.camera_begin_timeout_ms))
        except CAMERA_ERRORS:
            return ReferenceTicket(None, None)

    def _abandon(self, ticket_id: str) -> None:
        camera = self.p.camera
        if camera is None:
            return
        try:
            camera.abandon(ticket_id, timeout=self.ms(self.p.settings.camera_call_timeout_ms))
        except CAMERA_ERRORS:
            return  # E06 expires the ticket at its TTL and discards any late result (4.3)
