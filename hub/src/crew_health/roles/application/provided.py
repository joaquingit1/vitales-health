"""Ports the roles module provides: ClinicalAuthorityQuery (SYSTEM 8.4), RoleQueryPort, SkillFacts (SYSTEM 8.2),
SkillHolderPort (E28) and DrillReader (E30). Each fails closed: a missing answer is no authority and no holder."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.guidance.application.ports import Fact, Known, OperatorFact, Unknown
from crew_health.help.application.ports import BroadcastRole
from crew_health.kernel.uow import Actor, ReadSnapshot, UnitOfWork
from crew_health.roles.application.ports import (
    AssignmentRecord,
    AuthorityDecision,
    DoseAuthorityContext,
    DrillRef,
    RoleAssignmentRef,
)
from crew_health.roles.application.world import Deps, call, read_world, record_for
from crew_health.roles.domain.acting import decide_authority, officer_statuses
from crew_health.roles.domain.errors import PortUnavailable, RolesError
from crew_health.roles.domain.lifecycle import Assignment

_ENGINE = Actor("engine", "roles")

type Action = Literal["urgency_disposition", "dose_confirmation", "record_review", "clinician_assessed"]


@dataclass(frozen=True, slots=True)
class BroadcastAssignment:
    role_assignment_id: str
    role_id: str
    label: str
    person_id: str
    display_name: str
    state: Literal["proposed", "accepted", "changed"]


def _ref(a: Assignment) -> RoleAssignmentRef:
    return RoleAssignmentRef(
        role_assignment_id=a.role_assignment_id,
        help_session_id=a.context_id if a.context_kind == "help_session" else None,
        plan_run_id=a.context_id if a.context_kind == "plan_run" else None,
        role_id=a.role_id,
        person_id=a.person_id,
        state=a.state,
        acting_medical_officer=a.acting_since is not None and a.acting_ended_at is None,
    )


class RolesProvided:
    """One object implementing every provided protocol over the module's repository and consumed ports."""

    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    def roles_for_session(self, help_session_id: str) -> Sequence[BroadcastRole]:
        """P0 operational assignments only, in configured role order (E26 3.4, E31 3.4)."""
        at = self.deps.clock.now()
        roster = tuple(call("roster", lambda: self.deps.roster.crew(at)))
        names = {member.person_id: member.display_name for member in roster}
        if len(roster) > self.deps.settings.roster_max or len(names) != len(roster):
            raise PortUnavailable("roster")
        with self.deps.snapshots.snapshot() as view:  # a pure read: no write transaction (SYSTEM 5.7)
            repo = self.deps.repository(view.connection)
            cfg = self.deps.config_cache().get(view.release)
            order = {role_id: i for i, role_id in enumerate(cfg.rules.roles)}
            current = sorted(
                repo.current("help_session", help_session_id),
                key=lambda a: (order.get(a.role_id, len(order)), a.role_assignment_id),
            )
            result: list[BroadcastRole] = []
            for assignment in current:
                name = names.get(assignment.person_id)
                if name is None:
                    raise PortUnavailable("roster")
                result.append(
                    BroadcastAssignment(
                        assignment.role_assignment_id,
                        assignment.role_id,
                        assignment.label,
                        assignment.person_id,
                        name,
                        assignment.state,
                    )
                )
            return tuple(result)

    # SYSTEM 8.4 ClinicalAuthorityQuery.authority; also MedicalOfficerQuery.acting (SYSTEM 4.4)
    def authority(
        self,
        snapshot: ReadSnapshot | UnitOfWork,
        *,
        person_id: str,
        subject_person_id: str,
        action: Action,
        dose: DoseAuthorityContext | None,
        at: datetime,
    ) -> AuthorityDecision:
        try:
            return self._authority(snapshot, person_id, subject_person_id, action, at)
        except RolesError:
            return AuthorityDecision("none", None, "facts_unavailable")  # missing grants deny (SYSTEM 8.4)

    def _authority(
        self, snapshot: ReadSnapshot | UnitOfWork, person_id: str, subject: str, action: Action, at: datetime
    ) -> AuthorityDecision:
        world = read_world(self.deps, at)
        repo = self.deps.repository(snapshot.connection)
        cfg = self.deps.config_cache().get(snapshot.release)
        member = world.member(person_id)
        duties = [
            a
            for a in repo.current_medical_officer(person_id, self.deps.settings.page_max)
            if a.acting_since is not None and a.acting_ended_at is None
        ]
        acting: Assignment | None = None
        context_open = False
        for duty in duties:
            ctx = repo.context(duty.context_kind, duty.context_id)
            if ctx is not None and ctx.subject_person_id == subject:
                acting, context_open = duty, ctx.open
                break
        statuses = officer_statuses(cfg.rules, world.people, subject, world.locations)
        decided = decide_authority(
            person_id=person_id,
            subject_person_id=subject,
            action=action,
            person_rank=None if member is None else member.medical_officer_rank,
            acting=acting,
            context_open=context_open,
            statuses=statuses,
        )
        return AuthorityDecision(decided.kind, decided.role_assignment_id, decided.reason)

    # RoleQueryPort
    def plan_run_person_ids(self, plan_run_id: str) -> frozenset[str]:
        with self.deps.uow.begin(actor=_ENGINE, correlation_id=self.deps.ids.new("cor")) as uow:
            repo = self.deps.repository(uow.connection)
            return frozenset(a.person_id for a in repo.current("plan_run", plan_run_id))

    def medical_officer_assignments(self, subject_person_id: str) -> Sequence[RoleAssignmentRef]:
        with self.deps.uow.begin(actor=_ENGINE, correlation_id=self.deps.ids.new("cor")) as uow:
            repo = self.deps.repository(uow.connection)
            found: list[RoleAssignmentRef] = []
            for ctx in repo.open_contexts(self.deps.settings.page_max):
                if ctx.subject_person_id != subject_person_id:
                    continue
                found.extend(
                    _ref(a) for a in repo.current(ctx.kind, ctx.context_id) if a.role_id == "medical_officer"
                )
            return found

    # SkillFacts (SYSTEM 8.2, guidance's consumer-owned protocol)
    def operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> Fact[tuple[OperatorFact, ...]]:
        """The whole roster with every catalog skill's freshness. The guidance rule excludes the subject and checks
        awake, on site and EVA itself. A failed read is Unknown, never an empty roster."""
        try:
            return Known(self._operators(snapshot, subject_person_id, patient_location_id))
        except PortUnavailable as error:
            return Unknown("roles", error.port)

    def _operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> tuple[OperatorFact, ...]:
        at = snapshot.now
        world = read_world(self.deps, at)
        repo = self.deps.repository(snapshot.connection)
        cfg = self.deps.config_cache().get(snapshot.release)
        target = patient_location_id
        if target is None and subject_person_id is not None:
            target = world.locations.get(subject_person_id)
        facts: list[OperatorFact] = []
        for p in sorted(world.people, key=lambda p: (p.roster_index, p.person_id)):
            where = world.locations.get(p.person_id)
            # Same policy as the acting officer rule: away only when both places are known and differ.
            away = where is not None and target is not None and where != target
            fresh = {
                s: _held(record_for(repo, cfg, p.person_id, s, world.mission, at).freshness)
                for s in sorted(cfg.skills)
            }
            facts.append(
                OperatorFact(
                    person_id=p.person_id,
                    roster_order=p.roster_index,
                    awake=not p.asleep,
                    on_site=not p.on_eva and not away,
                    on_eva=p.on_eva,
                    officer_rank=p.officer_rank,
                    skill_freshness=fresh,
                )
            )
        return tuple(facts)

    # SkillHolderPort (E28)
    def holders(self, skill_id: str, at: datetime) -> Sequence[str]:
        world = read_world(self.deps, at)
        with self.deps.uow.begin(actor=_ENGINE, correlation_id=self.deps.ids.new("cor")) as uow:
            repo = self.deps.repository(uow.connection)
            cfg = self.deps.config_cache().get(uow.release)
            if skill_id not in cfg.skills:
                return ()
            people = sorted(world.people, key=lambda p: (p.roster_index, p.person_id))
            return tuple(
                p.person_id
                for p in people
                if record_for(repo, cfg, p.person_id, skill_id, world.mission, at).is_holder
            )

    # RoleAssignmentReader (E30 X12)
    def get(self, role_assignment_id: str) -> AssignmentRecord | None:
        # A pure read (SYSTEM 5.7): E30 asks inside its own write transaction, so a second one would wait on it.
        with self.deps.snapshots.snapshot() as view:
            a = self.deps.repository(view.connection).get(role_assignment_id)
        if a is None:
            return None
        return AssignmentRecord(
            role_assignment_id=a.role_assignment_id,
            person_id=a.person_id,
            state=a.state,
            skill_id=a.skill_id,
            plan_run_id=a.context_id if a.context_kind == "plan_run" else None,
            help_session_id=a.context_id if a.context_kind == "help_session" else None,
            # retired_at set, or replaced by a newer row: no longer current, so it never authorizes (fail closed)
            retired=not a.current,
        )

    # DrillReader (E30)
    def running_drill(self, drill_id: str) -> DrillRef | None:
        with self.deps.uow.begin(actor=_ENGINE, correlation_id=self.deps.ids.new("cor")) as uow:
            drill = self.deps.repository(uow.connection).get_drill(drill_id)
            if drill is None or drill.state != "running" or uow.now >= drill.day_end:
                return None
            return DrillRef(
                drill.drill_id, drill.scenario_id, drill.skill_ids, drill.participant_ids, drill.origin_mode
            )


def _held(freshness: str) -> Literal["fresh", "due", "stale"]:
    """SYSTEM 8.2 has no `never`: a person who never practiced is stale for guidance (holds nothing)."""
    if freshness == "fresh":
        return "fresh"
    if freshness == "due":
        return "due"
    return "stale"
