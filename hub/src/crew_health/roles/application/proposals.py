"""Proposals: event handlers apply the hard exclusions at once; the recompute worker writes the engine's diff.

Design 3.5, 4.1 and 5.2. The handlers need no port call, so a port timeout can never leave the subject or a walker in
an on-site role (I-R1, I-R2). The worker calls ports outside any transaction, then writes the diff in one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from crew_health.contracts_gen.enums import ConfigName, EventType
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.roles.application import wire
from crew_health.roles.application.ports import HelpSessionSnapshot, PlanRunSnapshot
from crew_health.roles.application.repository import ContextRow, RecomputeRow, RolesRepository, UnfilledRow
from crew_health.roles.application.world import (
    CREW,
    Deps,
    World,
    audit,
    call,
    emit,
    holders,
    label_at,
    read_world,
    record_for,
)
from crew_health.roles.domain.acting import officer_statuses
from crew_health.roles.domain.engine import (
    ContextKind,
    EngineInput,
    Proposal,
    ReasonDraft,
    need_key,
    plan_needs,
    propose,
    propose_plan_run,
)
from crew_health.roles.domain.errors import PortUnavailable, RulesInvalid
from crew_health.roles.domain.lifecycle import Assignment, ContextReason, diff
from crew_health.roles.domain.rules import RoleNeed, RolesConfig, render

ENGINE = Actor("engine", "roles")
_BACKOFF_CAP = 6


@dataclass(frozen=True, slots=True)
class ContextFacts:
    """What the engine needs from E26 or E28 for one context."""

    kind: ContextKind
    context_id: str
    open: bool
    subject_person_id: str | None
    opener_person_id: str | None
    flow_id: str | None
    skill_steps: tuple[str, ...]
    pins: Mapping[ConfigName, ConfigDocumentView]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _subject(payload: Mapping[str, object]) -> str | None:
    subject = payload.get("subject")
    return _text(subject.get("person_id")) if isinstance(subject, Mapping) else None


class Proposals:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    # ------------------------------------------------------------------------------------------------- handlers
    def on_help_session_changed(self, uow: UnitOfWork, payload: Mapping[str, object]) -> None:
        self._on_context(
            uow,
            "help_session",
            _text(payload.get("help_session_id")),
            payload,
            payload.get("state") == "open",
        )

    def on_plan_run_changed(self, uow: UnitOfWork, payload: Mapping[str, object]) -> None:
        self._on_context(
            uow, "plan_run", _text(payload.get("plan_run_id")), payload, payload.get("state") != "closed"
        )

    def _on_context(
        self,
        uow: UnitOfWork,
        kind: ContextKind,
        context_id: str | None,
        payload: Mapping[str, object],
        is_open: bool,
    ) -> None:
        revision = payload.get("revision")
        if context_id is None or isinstance(revision, bool) or not isinstance(revision, int):
            return  # an unreadable payload changes nothing; the startup sweep and the next event recompute
        repo = self.deps.repository(uow.connection)
        row = repo.context(kind, context_id)
        if row is not None and row.revision is not None and revision <= row.revision:
            return  # a delayed or replayed event never overwrites newer state (AGENTS 6, Time)
        subject = _subject(payload)
        for a in repo.current(kind, context_id):
            if subject is not None and a.person_id == subject:
                self._retire(uow, repo, a, "ineligible_subject")
            elif not is_open and a.acting_since is not None and a.acting_ended_at is None:
                self._end_acting(uow, repo, a, "context_closed")
        repo.put_context(
            ContextRow(
                kind,
                context_id,
                0 if row is None else row.proposal_seq,
                subject,
                is_open,
                None if row is None else row.as_of,
                None if row is None else row.degraded_at,
                revision,
                None if row is None else row.next_check_at,
            )
        )
        if is_open:
            repo.request_recompute(kind, context_id, reason="context_changed", at=uow.now)

    def on_overlay_changed(self, uow: UnitOfWork, payload: Mapping[str, object]) -> None:
        person_id = _text(payload.get("person_id"))
        repo = self.deps.repository(uow.connection)
        if person_id is not None and payload.get("overlay") == "eva" and payload.get("state") == "active":
            on_site = self._on_site_roles(uow)
            for a in repo.current_for_person(person_id, self.deps.settings.page_max):
                if on_site is None or a.role_id in on_site:
                    self._retire(uow, repo, a, "ineligible_eva")
        for ctx in repo.open_contexts(self.deps.settings.page_max):
            repo.request_recompute(ctx.kind, ctx.context_id, reason="overlay_changed", at=uow.now)

    def on_practice(self, uow: UnitOfWork) -> None:
        self.request_all(uow, "holders_changed")

    def on_release_activated(self, uow: UnitOfWork) -> None:
        """config.release_activated: role-rules, skills-catalog or ui-copy may have changed (design 3.5)."""
        self.request_all(uow, "release_activated")

    def on_environment_activated(self, uow: UnitOfWork) -> None:
        """environment.activated: mission mode or environment changed; holders follow the practice mode."""
        self.request_all(uow, "environment_activated")

    def request_all(self, uow: UnitOfWork, reason: str) -> int:
        repo = self.deps.repository(uow.connection)
        contexts = repo.open_contexts(self.deps.settings.page_max)
        for ctx in contexts:
            repo.request_recompute(ctx.kind, ctx.context_id, reason=reason, at=uow.now)
        return len(contexts)

    def startup_sweep(self) -> int:
        """Design 5.2 startup: every open context gets a recompute request."""
        with self.deps.uow.begin(actor=ENGINE, correlation_id=self.deps.ids.new("cor")) as uow:
            return self.request_all(uow, "startup")

    def _freshness_timer(self, uow: UnitOfWork, repo: RolesRepository) -> None:
        """A holder going stale sends no event: contexts whose earliest holder stale_at passed are recomputed."""
        for ctx in repo.contexts_to_recheck(uow.now, self.deps.settings.recompute_batch):
            repo.request_recompute(ctx.kind, ctx.context_id, reason="freshness", at=uow.now)
            repo.put_context(replace(ctx, next_check_at=None))

    def _on_site_roles(self, uow: UnitOfWork) -> frozenset[str] | None:
        """On-site roles of the active rules; None (every role counts as on-site) when the rules cannot be read."""
        try:
            rules = self.deps.config_cache().get(uow.release).rules
        except RulesInvalid, PortUnavailable:
            return None
        return frozenset(r for r, d in rules.roles.items() if d.on_site)

    def _retire(self, uow: UnitOfWork, repo: RolesRepository, a: Assignment, reason: str) -> None:
        if not repo.retire(
            a.role_assignment_id, at=uow.now, reason=reason, replaced_by_id=None, expected_revision=a.revision
        ):
            return
        retired = replace(a, retired_at=uow.now, retired_reason=reason, revision=a.revision + 1)
        emit(uow, EventType.ROLE_CHANGED, wire.assignment(retired, []), scope=CREW, ref=a.role_assignment_id)
        audit(
            uow, self.deps, "roles.retired", {"role_assignment_id": a.role_assignment_id}, {"reason": reason}
        )
        audit_acting_end(uow, self.deps, a, reason)

    def _end_acting(self, uow: UnitOfWork, repo: RolesRepository, a: Assignment, why: str) -> Assignment:
        """Design 3.8 (d): the acting period ends; a new one needs a new acceptance."""
        ended = replace(a, acting_ended_at=uow.now, revision=a.revision + 1)
        if not repo.save(ended, a.revision):
            raise PortUnavailable("storage_revision")
        audit_acting_end(uow, self.deps, a, why)
        emit(uow, EventType.ROLE_CHANGED, wire.assignment(ended, []), scope=CREW, ref=a.role_assignment_id)
        return ended

    # --------------------------------------------------------------------------------------------------- worker
    def run_due(self) -> int:
        """One worker pass over at most `roles.recompute_batch` due contexts; returns how many were written."""
        now = self.deps.clock.now()
        with self.deps.uow.begin(actor=ENGINE, correlation_id=self.deps.ids.new("cor")) as uow:
            repo = self.deps.repository(uow.connection)
            self._freshness_timer(uow, repo)
            due = repo.due_recomputes(now, self.deps.settings.recompute_batch)
        done = 0
        for row in due:
            try:
                facts = self._facts(row)
                world = None if facts is None or not facts.open else read_world(self.deps, now)
            except (PortUnavailable, RulesInvalid) as error:
                self._failed(row, error)
                continue
            try:
                written = self._write(row, facts, world)
            except (PortUnavailable, RulesInvalid) as error:
                self._failed(row, error)
                continue
            done += 1 if written else 0
        return done

    def _facts(self, row: RecomputeRow) -> ContextFacts | None:
        if row.kind == "help_session":
            snap = call("help_sessions", lambda: self.deps.help_sessions.snapshot(row.context_id))
            return None if snap is None else self._help_facts(snap)
        reader = self.deps.plan_runs
        if reader is None:
            raise PortUnavailable("plan_runs")
        run = call("plan_runs", lambda: reader.state(row.context_id))
        return None if run is None else self._plan_facts(run)

    def _help_facts(self, snap: HelpSessionSnapshot) -> ContextFacts:
        return ContextFacts(
            "help_session",
            snap.help_session_id,
            snap.open,
            snap.subject_person_id,
            snap.opener_person_id,
            snap.flow_id,
            (),
            {},
        )

    def _plan_facts(self, run: PlanRunSnapshot) -> ContextFacts:
        reader = self.deps.config_reader
        if reader is None:
            raise PortUnavailable("config_reader")
        pins = {
            ConfigName.ROLE_RULES: call(
                "config", lambda: reader.document(ConfigName.ROLE_RULES, run.role_rules_version)
            ),
            ConfigName.SKILLS_CATALOG: call(
                "config", lambda: reader.document(ConfigName.SKILLS_CATALOG, run.skills_catalog_version)
            ),
        }
        skills = tuple(skill for _, skill in run.open_skill_steps)
        return ContextFacts(
            "plan_run", run.plan_run_id, run.state == "open", run.subject_person_id, None, None, skills, pins
        )

    def _write(self, row: RecomputeRow, facts: ContextFacts | None, world: World | None) -> bool:
        """Writes the diff only when no newer request arrived since the facts were read; otherwise the newer
        request stays and the next pass reads fresh facts (a walk starting mid-pass never lands on site)."""
        with self.deps.uow.begin(actor=ENGINE, correlation_id=self.deps.ids.new("cor")) as uow:
            repo = self.deps.repository(uow.connection)
            if repo.recompute_version(row.kind, row.context_id) != row.version:
                return False
            if facts is None or world is None or not facts.open:
                self._close(uow, repo, row, facts)
                return True
            cfg = self.deps.config_cache().get(uow.release, facts.pins)
            ctx = repo.context(row.kind, row.context_id)
            seq = 0 if ctx is None else ctx.proposal_seq
            if seq >= self.deps.settings.max_proposals_per_context:
                self._degrade(uow, repo, row, "capacity")
                repo.finish_recompute(row)
                return True
            self._check_acting(uow, repo, cfg, facts, world)
            current = repo.current(row.kind, row.context_id)
            proposal, deadline = self._propose(cfg, repo, facts, world, current)
            self._apply(uow, repo, cfg, facts, world, current, proposal, seq + 1, deadline)
            repo.finish_recompute(row)
            return True

    def _check_acting(
        self, uow: UnitOfWork, repo: RolesRepository, cfg: RolesConfig, facts: ContextFacts, world: World
    ) -> None:
        """Design 3.8 (d): an acting period ends once any officer is available again."""
        statuses = officer_statuses(cfg.rules, world.people, facts.subject_person_id, world.locations)
        if all(s.unavailable for s in statuses):
            return
        for a in repo.current(facts.kind, facts.context_id):
            if a.acting_since is not None and a.acting_ended_at is None:
                self._end_acting(uow, repo, a, "officer_available")

    def _next_check(
        self,
        repo: RolesRepository,
        cfg: RolesConfig,
        world: World,
        found: Mapping[str, Mapping[str, datetime]],
    ) -> datetime | None:
        """The earliest stale_at among the holders this proposal used: the freshness timer's deadline."""
        deadlines: list[datetime] = []
        for skill_id, people in found.items():
            for person_id in people:
                record = record_for(repo, cfg, person_id, skill_id, world.mission, world.at)
                if record.stale_at is not None:
                    deadlines.append(record.stale_at)
        return min(deadlines) if deadlines else None

    def _close(
        self, uow: UnitOfWork, repo: RolesRepository, row: RecomputeRow, facts: ContextFacts | None
    ) -> None:
        ctx = repo.context(row.kind, row.context_id)
        if ctx is not None:
            repo.put_context(replace(ctx, open=False))
        repo.finish_recompute(row)

    def _needs(self, cfg: RolesConfig, facts: ContextFacts) -> tuple[RoleNeed, ...]:
        if facts.kind == "help_session":
            return cfg.rules.needs_for_flow(facts.flow_id)
        return plan_needs(cfg.rules, facts.skill_steps, {s: d.name for s, d in cfg.skills.items()})

    def _propose(
        self,
        cfg: RolesConfig,
        repo: RolesRepository,
        facts: ContextFacts,
        world: World,
        current: Sequence[Assignment],
    ) -> tuple[Proposal, datetime | None]:
        needs = self._needs(cfg, facts)
        skill_ids = {n.skill_id for n in needs if n.skill_id is not None}
        found = holders(repo, cfg, world.mission, [p.person_id for p in world.people], skill_ids, world.at)
        deadline = self._next_check(repo, cfg, world, found)
        inp = EngineInput(
            subject_person_id=facts.subject_person_id,
            opener_person_id=facts.opener_person_id,
            needs=needs,
            people=world.people,
            holders=found,
            decided=tuple(a.decided() for a in current if a.human_decided),
            now=world.at,
            eva_word=cfg.eva_words.get(world.mission.active_environment_id),
            skill_short={s: d.short_name for s, d in cfg.skills.items()},
        )
        engine = propose if facts.kind == "help_session" else propose_plan_run
        return engine(cfg.rules, inp, label_at(self.deps)), deadline

    def _apply(
        self,
        uow: UnitOfWork,
        repo: RolesRepository,
        cfg: RolesConfig,
        facts: ContextFacts,
        world: World,
        current: Sequence[Assignment],
        proposal: Proposal,
        seq: int,
        deadline: datetime | None,
    ) -> None:
        rendered = [
            (_render(cfg, p.reason), tuple(ContextReason(c.code, _render(cfg, c)) for c in p.context))
            for p in proposal.proposed
        ]
        change = diff(current, proposal, rendered)
        by_id = {a.role_assignment_id: a for a in current}
        new_ids = {i: self.deps.ids.new("rol") for i in change.insert}
        replaced = dict(change.replace)
        filled_keys = {need_key(proposal.proposed[i].need): new_ids[i] for i in change.insert}
        for old_id, reason in change.retire:
            old = by_id[old_id]
            successor = filled_keys.get(need_key(old.decided())) if old.human_decided else None
            self._end(uow, repo, old, reason, successor)
        for old_id, index in replaced.items():
            self._end(uow, repo, by_id[old_id], "replaced", new_ids[index])
        for index in change.insert:
            item, (text, context) = proposal.proposed[index], rendered[index]
            person = world.member(item.person_id)
            if person is None:
                raise PortUnavailable("roster")
            new = Assignment(
                role_assignment_id=new_ids[index],
                revision=1,
                context_kind=facts.kind,
                context_id=facts.context_id,
                role_id=item.need.role_id,
                label=cfg.rules.roles[item.need.role_id].label,
                person_id=item.person_id,
                person_display_name=person.display_name,
                skill_id=item.need.skill_id,
                task_text=item.need.task_text,
                reason_code=item.reason.code,
                reason_text=text,
                context_reasons=context,
                state="proposed",
                proposed_at=uow.now,
                accepted_at=None,
                changed_by=None,
                changed_at=None,
                replaced_by_id=None,
                retired_at=None,
                retired_reason=None,
                acting_since=None,
                acting_ended_at=None,
                config_release_id=uow.release.release_id,
            )
            repo.insert(new, seq)
            emit(
                uow, EventType.ROLE_PROPOSED, wire.assignment(new, []), scope=CREW, ref=new.role_assignment_id
            )
        unfilled = [
            UnfilledRow(n.role_id, cfg.rules.roles[n.role_id].label, n.required) for n in proposal.unfilled
        ]
        changed = bool(change.insert or change.retire or change.replace) or unfilled != repo.unfilled(
            facts.kind, facts.context_id
        )
        repo.set_unfilled(facts.kind, facts.context_id, unfilled)
        ctx = repo.context(facts.kind, facts.context_id)
        repo.put_context(
            ContextRow(
                facts.kind,
                facts.context_id,
                seq if changed else (0 if ctx is None else ctx.proposal_seq),
                facts.subject_person_id if ctx is None else ctx.subject_person_id,
                True,
                uow.now if changed or ctx is None or ctx.as_of is None else ctx.as_of,
                None,
                None if ctx is None else ctx.revision,
                deadline,
            )
        )
        if changed:
            audit(uow, self.deps, "roles.proposed", {facts.kind + "_id": facts.context_id}, {"seq": seq})

    def _end(
        self, uow: UnitOfWork, repo: RolesRepository, old: Assignment, reason: str, successor: str | None
    ) -> None:
        if not repo.retire(
            old.role_assignment_id,
            at=uow.now,
            reason=reason,
            replaced_by_id=successor,
            expected_revision=old.revision,
        ):
            raise PortUnavailable("storage_revision")  # a racing action won; retry on the next pass
        ended = replace(old, replaced_by_id=successor, revision=old.revision + 1)
        if successor is None:
            ended = replace(ended, retired_at=uow.now, retired_reason=reason)
        emit(uow, EventType.ROLE_CHANGED, wire.assignment(ended, []), scope=CREW, ref=old.role_assignment_id)
        audit(
            uow,
            self.deps,
            "roles.retired",
            {"role_assignment_id": old.role_assignment_id},
            {"reason": reason},
        )
        audit_acting_end(uow, self.deps, old, reason)

    # ------------------------------------------------------------------------------------------------- failures
    def _failed(self, row: RecomputeRow, error: Exception) -> None:
        settings = self.deps.settings
        with self.deps.uow.begin(actor=ENGINE, correlation_id=self.deps.ids.new("cor")) as uow:
            repo = self.deps.repository(uow.connection)
            delay = min(
                settings.retry_base_seconds * 2 ** min(row.attempts, _BACKOFF_CAP), settings.retry_max_seconds
            )
            repo.fail_recompute(
                row, next_attempt_at=uow.now + timedelta(seconds=delay), error=type(error).__name__
            )
            if uow.now - row.first_requested_at > timedelta(seconds=settings.proposal_overdue_seconds):
                self._degrade(uow, repo, row, "overdue")

    def _config_or_none(self, uow: UnitOfWork) -> RolesConfig | None:
        try:
            return self.deps.config_cache().get(uow.release)
        except RulesInvalid, PortUnavailable:
            return None

    def _degrade(self, uow: UnitOfWork, repo: RolesRepository, row: RecomputeRow, why: str) -> None:
        """Proposed rows leave (their roles show unfilled); human decisions stay (design 5.2)."""
        ctx = repo.context(row.kind, row.context_id)
        if ctx is not None and ctx.degraded_at is not None:
            return
        unfilled = list(repo.unfilled(row.kind, row.context_id))
        cfg = self._config_or_none(uow)
        for a in repo.current(row.kind, row.context_id):
            if a.state != "proposed" or a.changed_by is not None:
                continue
            if repo.retire(
                a.role_assignment_id,
                at=uow.now,
                reason="degraded",
                replaced_by_id=None,
                expected_revision=a.revision,
            ):
                gone = replace(a, retired_at=uow.now, retired_reason="degraded", revision=a.revision + 1)
                emit(
                    uow,
                    EventType.ROLE_CHANGED,
                    wire.assignment(gone, []),
                    scope=CREW,
                    ref=a.role_assignment_id,
                )
                audit_acting_end(uow, self.deps, a, "degraded")
                unfilled.append(UnfilledRow(a.role_id, a.label, _required(cfg, a)))
        repo.set_unfilled(row.kind, row.context_id, unfilled)
        if ctx is not None:
            repo.put_context(replace(ctx, degraded_at=uow.now))
        audit(uow, self.deps, "roles.proposal_degraded", {row.kind + "_id": row.context_id}, {"why": why})


def _render(cfg: RolesConfig, draft: ReasonDraft) -> str:
    template = cfg.rules.reason_phrases.get(draft.code)
    if template is None:
        raise RulesInvalid((("/reason_phrases", f"no phrase for {draft.code}"),))
    return render(template, draft.values, cfg.words.unknown)


def changed_by_text(cfg: RolesConfig, person_name: str | None) -> str:
    return _render(cfg, ReasonDraft("changed_by_helper", {"person_name": person_name}))


def label_of(cfg: RolesConfig, role_id: str) -> str:
    role = cfg.rules.roles.get(role_id)
    return role.label if role is not None else cfg.words.unknown


def _required(cfg: RolesConfig | None, a: Assignment) -> bool:
    """Whether the need a degraded row filled was required; a plan step, or a need the rules no longer list, counts
    as required (shown loudly, never hidden)."""
    if cfg is None or a.context_kind == "plan_run":
        return True
    key = need_key(a.decided())
    for needs in (*cfg.rules.flow_roles.values(), cfg.rules.default_roles):
        for need in needs:
            if need_key(need) == key:
                return need.required
    return True


def audit_acting_end(uow: UnitOfWork, deps: Deps, a: Assignment, why: str) -> None:
    """Design 3.8: the end of an acting period is audited whatever ends it (retire, reassign, officer back)."""
    if a.acting_since is not None and a.acting_ended_at is None:
        audit(uow, deps, "roles.acting_ended", {"role_assignment_id": a.role_assignment_id}, {"why": why})
