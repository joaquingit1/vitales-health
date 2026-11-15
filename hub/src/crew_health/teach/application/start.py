"""StartGuidedCheck (E30 4.2): context validation, pins, origin and record routing, eligibility, then one unit of
work holding the new check, its audit entry and the idempotency result. No creation event exists (CONTRACTS 18)."""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import SessionContext
from crew_health.teach.application import access
from crew_health.teach.application.commands import CommandMeta, Response, TeachCore
from crew_health.teach.application.content import ContentLoader, PinnedContent
from crew_health.teach.application.ports import (
    ContentRef,
    GuidanceRequest,
    GuidedStepRef,
    OriginMode,
    Requirements,
)
from crew_health.teach.domain.capture import ClockReading
from crew_health.teach.domain.catalog import SkillDefinition
from crew_health.teach.domain.errors import ContextInapplicable, InvalidValue, NotEligible
from crew_health.teach.domain.guided_check import GuidedCheck

OPERATION = "startGuidedCheck"
LIVE: OriginMode = "LIVE"  # beats through the HTTP API from a signed-in person are LIVE (3.6)


@dataclass(frozen=True, slots=True)
class StartInput:
    skill_id: str
    helper_person_id: str
    subject_person_id: str
    plan_run_id: str | None
    drill_id: str | None
    role_assignment_id: str | None
    config_release: str | None


@dataclass(frozen=True, slots=True)
class _Context:
    pins: dict[str, str]
    release_id: str
    environment_id: str | None
    episode_id: str | None
    plan_step_id: str | None


class StartGuidedCheck(TeachCore):
    def __call__(self, meta: CommandMeta, data: StartInput) -> Response:
        access.require_actor(meta.session)
        identity = self.identity(meta, OPERATION, "guided-checks")
        with self.unit(meta, identity) as uow:
            replay = self.replay(uow, identity, meta.session)
            if replay is not None:
                return replay
            self._people(meta.session, data)
            context = self._context(data)
            content = self.p.content.load(context.pins)
            skill = self._skill(content, data, context)
            self._eligible(skill, data, context)
            measured = self._route(data.subject_person_id)
            if self.p.repository.count_active(uow, data.subject_person_id, data.skill_id) > 0:
                raise ContextInapplicable("active_check_exists")
            if self.p.repository.count_active(uow, None, None) >= self.p.settings.max_active_checks:
                raise ContextInapplicable("max_active_checks")
            r = self.reading()
            check = self._new(meta.session, data, context, skill, measured, r)
            self.p.repository.add(uow, check)
            self.audit(uow, meta.session, "guided_check_started", check)
            self.remember(uow, identity, check, 201)
            return self.response(check, meta.session, 201, r)

    def _people(self, session: SessionContext, data: StartInput) -> None:
        if data.drill_id is not None:
            raise ContextInapplicable("drill_checks_not_built")  # 4.4 DrillCheckRuntime is not built (gap)
        if data.helper_person_id == data.subject_person_id:
            raise NotEligible("helper_is_subject")
        for field, person_id in (
            ("/helper_person_id", data.helper_person_id),
            ("/subject_person_id", data.subject_person_id),
        ):
            person = self.p.people.person(person_id)
            if person is None or not person.on_roster:
                raise InvalidValue("unknown_person", fields=(field,))
        if not self._may_start(session, data):
            raise access.forbidden()

    def _may_start(self, session: SessionContext, data: StartInput) -> bool:
        """Section 10 start rules narrowed by D9. The subject may ask a crewmate. Anyone else (a crewmate helper or a
        station) starts only as the helper named by an accepted role assignment for this plan run. A supplied
        assignment is always validated; a missing port or answer denies (fail closed)."""
        assigned = self._assignment_valid(data)
        if data.role_assignment_id is not None and not assigned:
            return False
        if access.is_station(session):
            return assigned
        person = session.person_id
        if person is None:
            return False
        if person == data.subject_person_id:
            return True
        return person == data.helper_person_id and assigned

    def _assignment_valid(self, data: StartInput) -> bool:
        roles = self.p.roles
        if roles is None or data.role_assignment_id is None or data.plan_run_id is None:
            return False
        try:
            ref = roles.get(data.role_assignment_id)
        except ProblemError:
            return False
        return (
            ref is not None
            and not ref.retired
            and ref.state == "accepted"
            and ref.person_id == data.helper_person_id
            and ref.skill_id == data.skill_id
            and ref.plan_run_id == data.plan_run_id
            and ref.help_session_id is None
        )

    def _context(self, data: StartInput) -> _Context:
        release = self.p.releases.active()
        pins = ContentLoader.pins_of(release)
        if data.plan_run_id is None:
            if data.config_release is not None and data.config_release != release.release_id:
                raise ProblemError(ErrorCode.CONFIG_VERSION_MISMATCH, ErrorCode.CONFIG_VERSION_MISMATCH.value)
            return _Context(pins, release.release_id, None, None, None)
        step = self._plan_step(data)
        pins["skills-catalog"] = step.skills_catalog_version
        pins["ui-copy"] = step.ui_copy_version
        return _Context(pins, release.release_id, step.environment_id, step.episode_id, step.step_id)

    def _plan_step(self, data: StartInput) -> GuidedStepRef:
        if self.p.plans is None or data.plan_run_id is None:
            raise ContextInapplicable("plans_unavailable")
        step = self.p.plans.guided_step(data.plan_run_id, data.skill_id)
        if step is None:
            raise ContextInapplicable("no_guided_step")
        if not step.run_open or not step.step_runnable:
            raise ContextInapplicable("run_closed")
        if step.subject_person_id != data.subject_person_id:
            raise InvalidValue("subject_not_run_subject", fields=("/subject_person_id",))
        return step

    def _skill(self, content: PinnedContent, data: StartInput, context: _Context) -> SkillDefinition:
        skill = content.catalog.skill(data.skill_id)
        if skill is None:
            raise InvalidValue("unknown_skill", fields=("/skill_id",))
        if skill.count_check is None:
            raise ContextInapplicable("skill_without_count_check")  # until X2
        step = skill.count_step(context.environment_id)
        if step is None or step.count_seconds is None:
            raise ContextInapplicable("no_count_step")
        if step.count_seconds > self.p.settings.max_count_seconds:
            raise ContextInapplicable("count_longer_than_bound")
        return skill

    def _eligible(self, skill: SkillDefinition, data: StartInput, context: _Context) -> None:
        """SYSTEM 8.2: a missing or refusing answer means no guidance (fail closed)."""
        request = GuidanceRequest(
            content=ContentRef("teach_step", skill.skill_id, context.pins["skills-catalog"]),
            purpose="guided_check",
            release_id=context.release_id,
            origin_mode=LIVE,
            subject_person_id=data.subject_person_id,
            patient_location_id=None,
            requirements=Requirements(frozenset(), {}, frozenset(), skill.who_may, None),
            fixed_first_screen=False,
            proposed_operator_id=data.helper_person_id,
        )
        with self.p.snapshots.snapshot() as snapshot:
            decision = self.p.eligibility.evaluate(snapshot, request)
        if decision.outcome in ("not_here", "not_supported"):
            raise ContextInapplicable("guidance_" + decision.outcome)
        if not decision.executable:
            raise NotEligible("guidance_not_executable")

    def _route(self, subject_id: str) -> str:
        try:
            return self.p.routing.record_for(subject_id, LIVE)
        except ProblemError as error:
            raise ContextInapplicable("routing_unavailable") from error

    def _new(
        self,
        session: SessionContext,
        data: StartInput,
        context: _Context,
        skill: SkillDefinition,
        measured: str,
        r: ClockReading,
    ) -> GuidedCheck:
        steps = skill.steps_for(context.environment_id)
        check = skill.count_check
        if not steps or check is None:
            raise ContextInapplicable("no_steps_here")
        return GuidedCheck(
            guided_check_id=self.p.ids.new("gck"),
            revision=1,
            skill_id=skill.skill_id,
            check_id=check.check_id,
            environment_id=context.environment_id,
            helper_id=data.helper_person_id,
            subject_id=data.subject_person_id,
            measured_record_id=measured,
            count_origin=LIVE,
            plan_run_id=data.plan_run_id,
            plan_step_id=context.plan_step_id,
            episode_id=context.episode_id,
            role_assignment_id=data.role_assignment_id,
            state="teaching",
            teach_step_no=steps[0].step_no,
            steps_done=[],
            attempt=None,
            helper_answer=None,
            answer_id=None,
            previous_answer_id=None,
            camera=None,
            result=None,
            difference_exact=None,
            count_exact=None,
            result_text=None,
            observation_id=None,
            experimental=check.experimental,
            pins=context.pins,
            config_release_id=context.release_id,
            created_at=r.utc,
            created_by_session=session.session_id,
        )
