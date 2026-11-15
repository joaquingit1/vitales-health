"""ActOnRole and the role views (E31 design 4.1, 3.4 and 3.8; CONTRACTS 24)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

from crew_health.contracts_gen.enums import ErrorCode, EventType, Role
from crew_health.kernel.commands import AcceptedReceipt, StoredResult, command_identity
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import SessionContext, UnitOfWork
from crew_health.roles.application import wire
from crew_health.roles.application.proposals import audit_acting_end, changed_by_text
from crew_health.roles.application.repository import RolesRepository
from crew_health.roles.application.world import CREW, Deps, World, audit, call, emit, read_world
from crew_health.roles.domain.acting import acting_condition, officer_statuses
from crew_health.roles.domain.engine import ContextKind, Person, exclusion
from crew_health.roles.domain.errors import (
    ContextClosed,
    ContextNotFound,
    KeyReused,
    NotCurrent,
    NotEligible,
    NotVisible,
    RevisionRequired,
    RoleNotFound,
    StaleRevision,
)
from crew_health.roles.domain.lifecycle import (
    Assignment,
    ChangedBy,
    accept,
    allowed_actions,
    reassign,
    require_human,
)
from crew_health.roles.domain.rules import RolesConfig

OFFICER_ROLES = frozenset({Role.CREW_MEDICAL_OFFICER, Role.STATION})


@dataclass(frozen=True, slots=True)
class RoleCommand:
    kind: ContextKind
    context_id: str
    action: Literal["accept", "reassign"]
    role_assignment_id: str
    person_id: str | None
    if_match: int | None
    key: str
    body: Mapping[str, object]
    config_release: str | None = None  # Config-Release header; help session actions ignore it (OpenAPI)


@dataclass(frozen=True, slots=True)
class ContextView:
    open: bool
    subject_person_id: str | None


def _fingerprint(body: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class RoleActions:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    # ------------------------------------------------------------------------------------------------ visibility
    def context(
        self, session: SessionContext, kind: ContextKind, context_id: str, repo: RolesRepository
    ) -> ContextView:
        """The context as its owner reports it; an unknown one is NOT_FOUND, an unreadable one NotVisible."""
        if kind == "help_session":
            snap = call("help_sessions", lambda: self.deps.help_sessions.snapshot(context_id))
            if snap is None:
                raise ContextNotFound(context_id)
            if not call("help_sessions", lambda: self.deps.help_sessions.readable_by(session, context_id)):
                raise NotVisible(context_id)
            return ContextView(snap.state == "open", snap.subject_person_id)
        reader = self.deps.plan_runs
        if reader is None:
            raise ContextNotFound(context_id)
        run = call("plan_runs", lambda: reader.state(context_id))
        if run is None:
            raise ContextNotFound(context_id)
        if not self._sees_plan_run(session, run.subject_person_id, repo.current("plan_run", context_id)):
            raise NotVisible(context_id)
        return ContextView(run.state == "open", run.subject_person_id)

    @staticmethod
    def _sees_plan_run(session: SessionContext, subject: str, current: list[Assignment]) -> bool:
        if session.roles & OFFICER_ROLES:
            return True
        person = session.person_id
        return person is not None and (person == subject or any(a.person_id == person for a in current))

    # ------------------------------------------------------------------------------------------------- command
    def act(self, session: SessionContext, cmd: RoleCommand) -> tuple[int, bytes]:
        require_human(session.actor.kind)  # the assistant never accepts or sets a role (AGENTS rule 4)
        world = read_world(self.deps, self.deps.clock.now())
        identity = command_identity(
            key=cmd.key,
            actor_ref=session.actor.id,
            operation="actOn" + ("HelpRole" if cmd.kind == "help_session" else "PlanRunRole"),
            target=cmd.context_id,
            parameters_json=json.dumps({"id": cmd.context_id}).encode(),
            body_fingerprint=_fingerprint(cmd.body),
            fingerprint_kind="sha256",
        )
        with self.deps.uow.begin(
            actor=session.actor, command=identity, correlation_id=self.deps.ids.new("cor")
        ) as uow:
            try:
                stored = self.deps.idempotency.lookup(uow, identity)
            except ProblemError as problem:
                if problem.code == ErrorCode.DUPLICATE_COMMAND:
                    raise KeyReused(cmd.key) from problem
                raise
            if stored is not None and stored.safe_body is not None:
                return stored.status, stored.safe_body
            body = self._apply(uow, session, cmd, world)
            receipt = AcceptedReceipt(
                cmd.key, identity.operation, cmd.context_id, self.deps.ids.new("rcp"), uow.now
            )
            self.deps.idempotency.record(
                uow, identity, StoredResult(receipt, 200, cmd.role_assignment_id, "ok", body)
            )
            return 200, body

    def _apply(self, uow: UnitOfWork, session: SessionContext, cmd: RoleCommand, world: World) -> bytes:
        repo = self.deps.repository(uow.connection)
        a = repo.get(cmd.role_assignment_id)
        if a is None or a.context_kind != cmd.kind or a.context_id != cmd.context_id:
            raise RoleNotFound(cmd.role_assignment_id)  # an ID from another context reveals nothing
        ctx = self.context(session, cmd.kind, cmd.context_id, repo)
        if not ctx.open:
            raise ContextClosed(cmd.kind, wire.assignment(a, []))
        if not a.current:
            now_current = next(
                (c for c in repo.current(cmd.kind, cmd.context_id) if c.role_id == a.role_id), None
            )
            raise NotCurrent(None if now_current is None else wire.assignment(now_current, []))
        active = uow.release.release_id
        if cmd.kind == "plan_run" and cmd.config_release is not None and cmd.config_release != active:
            raise ProblemError(
                ErrorCode.CONFIG_VERSION_MISMATCH,
                "the client rendered another release",
                current_state={"config_release_id": active},
            )
        if cmd.if_match is None:
            raise RevisionRequired(a.role_assignment_id)
        if cmd.if_match != a.revision:
            raise StaleRevision(
                wire.assignment(a, allowed_actions(a, session_person_id=session.person_id, context_open=True))
            )
        cfg = self.deps.config_cache().get(uow.release)
        if cmd.action == "accept":
            updated = self._accept(uow, session, a, cfg, world, ctx)
        else:
            updated = self._reassign(uow, session, a, cfg, world, ctx, cmd.person_id)
        if not repo.save(updated, a.revision):
            raise StaleRevision(wire.assignment(a, []))
        emit(uow, EventType.ROLE_CHANGED, wire.assignment(updated, []), scope=CREW, ref=a.role_assignment_id)
        audit(
            uow,
            self.deps,
            "roles." + cmd.action,
            {"role_assignment_id": a.role_assignment_id, "person_id": updated.person_id},
            {"revision": updated.revision},
        )
        view = wire.assignment(
            updated, allowed_actions(updated, session_person_id=session.person_id, context_open=True)
        )
        return json.dumps(view, sort_keys=True).encode()

    def _accept(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        a: Assignment,
        cfg: RolesConfig,
        world: World,
        ctx: ContextView,
    ) -> Assignment:
        updated = accept(a, person_id=session.person_id, actor_kind=session.actor.kind, now=uow.now)
        self._eligible(cfg, a.role_id, a.person_id, world, ctx)  # EI9 holds at acceptance too
        if a.role_id != "medical_officer":
            return updated
        member = world.member(a.person_id)
        if member is None or member.medical_officer_rank in (1, 2):
            return updated  # an officer holds authority by rank; no acting grant is recorded
        statuses = officer_statuses(cfg.rules, world.people, ctx.subject_person_id, world.locations)
        if not acting_condition(statuses):
            return updated  # an officer may be available, or the facts conflict: no grant (fail closed)
        audit(uow, self.deps, "roles.acting_started", {"role_assignment_id": a.role_assignment_id}, {})
        return replace(updated, acting_since=uow.now, acting_ended_at=None)

    def _reassign(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        a: Assignment,
        cfg: RolesConfig,
        world: World,
        ctx: ContextView,
        person_id: str | None,
    ) -> Assignment:
        target = self._eligible(cfg, a.role_id, person_id, world, ctx)
        audit_acting_end(uow, self.deps, a, "reassigned")
        actor_name = None if session.person_id is None else world.name(session.person_id)
        changed_by = ChangedBy(session.actor.kind, session.actor.id, actor_name)
        return reassign(
            a,
            person_id=target.person_id,
            person_display_name=target.display_name,
            changed_by=changed_by,
            reason_text=changed_by_text(cfg, actor_name),
            actor_kind=session.actor.kind,
            now=uow.now,
        )

    @staticmethod
    def _eligible(
        cfg: RolesConfig, role_id: str, person_id: str | None, world: World, ctx: ContextView
    ) -> Person:
        """The hard exclusions against the roster and overlays read for this command (EI9)."""
        target = (
            None if person_id is None else next((p for p in world.people if p.person_id == person_id), None)
        )
        if target is None:
            rule = cfg.rules.eligibility.get("on_roster")
            raise NotEligible("on_roster", rule.text if rule is not None else cfg.words.unknown)
        broken = exclusion(cfg.rules, role_id, target, ctx.subject_person_id)
        if broken is not None:
            rule = cfg.rules.eligibility.get(broken)
            raise NotEligible(broken, rule.text if rule is not None else cfg.words.unknown)
        return target

    # --------------------------------------------------------------------------------------------------- views
    def help_roles(self, session: SessionContext, help_session_id: str) -> dict[str, object]:
        with self.deps.uow.begin(actor=session.actor, correlation_id=self.deps.ids.new("cor")) as uow:
            repo = self.deps.repository(uow.connection)
            ctx = self.context(session, "help_session", help_session_id, repo)
            cfg = self.deps.config_cache().get(uow.release)
            order = {role_id: i for i, role_id in enumerate(cfg.rules.roles)}
            current = sorted(
                repo.current("help_session", help_session_id),
                key=lambda a: (order.get(a.role_id, len(order)), a.role_assignment_id),
            )
            row = repo.context("help_session", help_session_id)
            as_of = uow.now if row is None or row.as_of is None else row.as_of
            return {
                "help_session_id": help_session_id,
                "assignments": [
                    wire.assignment(
                        a, allowed_actions(a, session_person_id=session.person_id, context_open=ctx.open)
                    )
                    for a in current
                ],
                "unfilled": [
                    {"role_id": u.role_id, "label": u.label, "required": u.required}
                    for u in repo.unfilled("help_session", help_session_id)
                ],
                "as_of": wire.instant(as_of),
            }

    def plan_run_roles(
        self, session: SessionContext, plan_run_id: str, offset: int, limit: int
    ) -> dict[str, object]:
        limit = max(1, min(limit, self.deps.settings.page_max))
        with self.deps.uow.begin(actor=session.actor, correlation_id=self.deps.ids.new("cor")) as uow:
            repo = self.deps.repository(uow.connection)
            ctx = self.context(session, "plan_run", plan_run_id, repo)
            current = sorted(
                repo.current("plan_run", plan_run_id), key=lambda a: (a.skill_id or "", a.role_assignment_id)
            )
            page = current[offset : offset + limit]
            following = offset + limit
            return {
                "items": [
                    wire.assignment(
                        a, allowed_actions(a, session_person_id=session.person_id, context_open=ctx.open)
                    )
                    for a in page
                ],
                "page": {
                    "next_cursor": str(following) if following < len(current) else None,
                    "total": len(current),
                },
            }
