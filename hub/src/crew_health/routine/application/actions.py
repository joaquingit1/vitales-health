from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
from hashlib import sha256

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    StoredResult,
    canonical_json,
    command_identity,
)
from crew_health.kernel.uow import SessionContext, UnitOfWork
from crew_health.routine.application.deps import RoutineDeps
from crew_health.routine.application.engine import RoutineEngine
from crew_health.routine.application.ports import (
    OccurrenceActionBody,
    OccurrenceView,
    OverrideCreateBody,
    OverrideView,
)
from crew_health.routine.domain.bindings import Firing
from crew_health.routine.domain.errors import (
    ActionNotAllowed,
    ConfigVersionMismatch,
    DuplicateOverride,
    InvalidBody,
    InvalidTransition,
    NotFound,
    StaleVersion,
    Unauthorized,
    VersionRequired,
)
from crew_health.routine.domain.occurrence import Occurrence, Outcome, TransitionRecord, complete, later, skip
from crew_health.routine.domain.override import Override
from crew_health.routine.domain.release import (
    Binding,
    ReplaceCadence,
    UntilDays,
    UntilPersonStops,
    WarningRaisedOn,
)
from crew_health.routine.domain.vocabulary import MemberBasis


def json_body(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(k): json_body(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_body(v) for v in value]
    return value


def identity(
    session: SessionContext,
    key: str,
    operation: str,
    target: str,
    parameters: dict[str, object],
    body: object,
) -> CommandIdentity:
    return command_identity(
        key=key,
        actor_ref=session.actor.id,
        operation=operation,
        target=target,
        parameters_json=canonical_json(parameters),
        body_fingerprint=sha256(canonical_json(json_body(body))).hexdigest(),
        fingerprint_kind="sha256",
    )


def revision(current: int, expected: int | None) -> None:
    if expected is None:
        raise VersionRequired()
    if current != expected:
        raise StaleVersion(current)


def visible_override(override: Override, session: SessionContext) -> bool:
    if override.audience == "person":
        return override.person_id == session.person_id
    return session.person_id in override.member_ids or bool(
        session.roles & frozenset((Role.CREW_MEDICAL_OFFICER, Role.GROUND))
    )


class RoutineActions:
    def __init__(self, deps: RoutineDeps, engine: RoutineEngine) -> None:
        self.deps, self.engine = deps, engine

    def record(self, uow: UnitOfWork, cmd: CommandIdentity, status: int, ref: str) -> None:
        receipt = AcceptedReceipt(cmd.key, cmd.operation, cmd.target, self.deps.ids.new("rcp"), uow.now)
        self.deps.idempotency.record(uow, cmd, StoredResult(receipt, status, ref, "accepted", None))

    def view(self, uow: UnitOfWork, session: SessionContext, occ: Occurrence) -> OccurrenceView:
        from crew_health.routine.application.views import occurrence_view

        return occurrence_view(self.deps, self.engine, uow, occ, session, uow.now)

    def find(self, uow: UnitOfWork, session: SessionContext, identifier: str, *, virtual: bool) -> Occurrence:
        occ = self.deps.store.get(uow, identifier)
        if occ is None and virtual and session.person_id is not None:
            occ = next(
                (
                    o
                    for o in self.engine.today_occurrences(uow, session.person_id, uow.now)
                    if o.occurrence_id == identifier
                ),
                None,
            )
        if occ is None or occ.person_id != session.person_id:
            raise NotFound()
        return occ

    def act(
        self,
        session: SessionContext,
        occurrence_id: str,
        body: OccurrenceActionBody,
        *,
        if_match: int | None,
        idempotency_key: str,
        config_release: str | None = None,
    ) -> tuple[int, OccurrenceView]:
        if session.person_id is None or session.actor.kind == "assistant":
            raise Unauthorized("person_required")
        cmd = identity(
            session,
            idempotency_key,
            "actOnOccurrence",
            occurrence_id,
            {"occurrence_id": occurrence_id},
            asdict(body),
        )
        with self.deps.uow.begin(
            actor=session.actor, command=cmd, correlation_id=self.deps.ids.new("cor")
        ) as uow:
            stored = self.deps.idempotency.lookup(uow, cmd)
            if stored is not None:
                return stored.status, self.view(
                    uow, session, self.find(uow, session, occurrence_id, virtual=False)
                )
            occ = self.find(uow, session, occurrence_id, virtual=True)
            revision(occ.revision, if_match)
            if config_release is not None and config_release != uow.release.release_id:
                raise ConfigVersionMismatch(uow.release.release_id)
            if body.action == "complete" and (body.observation_ids or body.answer_ids):
                raise Unauthorized("linked_records_unverifiable")
            until, held = self.engine.hold(
                uow, occ, self.engine.release_for(uow, occ.config_release_id), uow.now
            )
            occ = replace(occ, due_until=until)
            if occ.state == "not_due":
                raise InvalidTransition(occ.revision)
            result = self.transition(uow, session, occ, body, held is not None)
            if self.deps.store.get(uow, occurrence_id) is None and not self.engine.insert(uow, occ):
                raise ActionNotAllowed("capacity")
            current = self.engine.change(uow, occ, result)
            self.record(uow, cmd, 200, current.occurrence_id)
            return 200, self.view(uow, session, current)

    def transition(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        occ: Occurrence,
        body: OccurrenceActionBody,
        held: bool,
    ) -> tuple[Occurrence, TransitionRecord]:
        result: tuple[Occurrence, TransitionRecord]
        if body.action == "complete":
            result = complete(
                occ,
                now=uow.now,
                completed_at=body.completed_at,
                outcome=Outcome((), (), body.note),
                actor_kind=session.actor.kind,
                actor_id=session.actor.id,
                day_start=self.engine.bounds(occ.mission_day, occ.clock_version)[0],
                guidance_release_id=occ.config_release_id,
            )
        elif body.action == "skip":
            if body.skip_reason is None:
                raise InvalidBody("/skip_reason", "required")
            result = skip(
                occ,
                now=uow.now,
                reason=body.skip_reason,
                note=body.note,
                linked=bool(body.observation_ids or body.answer_ids),
                actor_kind=session.actor.kind,
                actor_id=session.actor.id,
            )
        elif body.action == "later":
            if body.later_until is None:
                raise InvalidBody("/later_until", "required")
            result = later(
                occ,
                now=uow.now,
                later_until=body.later_until,
                held=held,
                actor_kind=session.actor.kind,
                actor_id=session.actor.id,
            )
        else:
            raise InvalidBody("/action", "unknown")
        return result

    def create_override(
        self,
        session: SessionContext,
        body: OverrideCreateBody,
        *,
        idempotency_key: str,
        config_release: str | None = None,
    ) -> tuple[int, OverrideView]:
        from crew_health.routine.application.views import override_view

        role = self.stop_role(session)
        cmd = identity(session, idempotency_key, "createOverride", "routine.overrides", {}, asdict(body))
        with self.deps.uow.begin(
            actor=session.actor, command=cmd, correlation_id=self.deps.ids.new("cor")
        ) as uow:
            stored = self.deps.idempotency.lookup(uow, cmd)
            if stored is not None:
                old = (
                    None
                    if stored.resource_ref is None
                    else self.deps.store.get_override(uow, stored.resource_ref)
                )
                if old is None or not visible_override(old, session):
                    raise NotFound()
                return stored.status, override_view(old, session)
            if config_release is not None and config_release != uow.release.release_id:
                raise ConfigVersionMismatch(uow.release.release_id)
            override = self.manual(uow, session, body, idempotency_key, role)
            if not self.deps.store.insert_override(uow, override):
                raise DuplicateOverride(override.override_id)
            self.engine.emit_override(uow, override)
            release, mission = self.engine.release(uow), self.engine.mission(uow)
            binding = self.manual_binding(body, override.trigger_text, override)
            firing = Firing(
                binding, "person", session.actor.id, uow.now, mission.mission_mode, override.member_ids, None
            )
            for person in override.member_ids:
                self.engine.inputs.apply_effect(uow, override, firing, person, mission, release)
            if override.ends_at is not None:
                self.engine.schedule(
                    uow,
                    "routine.ovr." + override.override_id,
                    "routine.override",
                    override.ends_at,
                    {"override_id": override.override_id, "kind": "ends"},
                )
            self.record(uow, cmd, 201, override.override_id)
            return 201, override_view(override, session)

    def stop_role(self, session: SessionContext) -> str:
        if session.actor.kind == "assistant":
            raise Unauthorized("human_required")
        if Role.CREW_MEDICAL_OFFICER in session.roles:
            return Role.CREW_MEDICAL_OFFICER.value
        if Role.GROUND in session.roles:
            return Role.GROUND.value
        raise Unauthorized("officer_required")

    def manual_binding(self, body: OverrideCreateBody, text: str, override: Override) -> Binding:
        return Binding(
            "routine.manual",
            "trigger",
            body.item_id,
            body.trigger_index,
            text,
            None,
            None,
            WarningRaisedOn(()),
            override.effect,
            UntilDays(body.until.days)
            if body.until.kind == "days" and body.until.days is not None
            else UntilPersonStops(override.stop_role or ""),
            override.audience,
            override.basis,
        )

    def manual_members(self, uow: UnitOfWork, person_id: str | None) -> tuple[tuple[str, MemberBasis], ...]:
        roster = (
            None if self.deps.roster is None else tuple(m.person_id for m in self.deps.roster.crew(uow.now))
        )
        if roster is not None and len(roster) > self.deps.settings.max_roster:
            raise ActionNotAllowed("capacity")
        if person_id is not None:
            if roster is not None and person_id not in roster:
                raise InvalidBody("/person_id", "not_on_roster")
            return ((person_id, "subject"),)
        else:
            if roster is None:
                raise ActionNotAllowed("roster_unavailable")
            return tuple((p, "site") for p in roster)

    def manual_capacity(
        self, uow: UnitOfWork, body: OverrideCreateBody, group: tuple[tuple[str, MemberBasis], ...]
    ) -> None:
        for person, _ in group:
            active = self.deps.store.active_overrides_for(
                uow, person, self.deps.settings.max_active_overrides_per_person
            )
            duplicate = next(
                (
                    o
                    for o in active
                    if o.item_id == body.item_id
                    and o.trigger_index == body.trigger_index
                    and o.person_id == body.person_id
                ),
                None,
            )
            if duplicate is not None:
                raise DuplicateOverride(duplicate.override_id)
            if len(active) >= self.deps.settings.max_active_overrides_per_person:
                raise ActionNotAllowed("capacity")

    def manual(
        self, uow: UnitOfWork, session: SessionContext, body: OverrideCreateBody, key: str, role: str
    ) -> Override:
        release, mission = self.engine.release(uow), self.engine.mission(uow)
        item = release.item(body.item_id)
        if item is None:
            raise InvalidBody("/item_id", "unknown")
        trigger = next((t for t in item.triggers if t.index == body.trigger_index), None)
        if trigger is None:
            raise InvalidBody("/trigger_index", "unknown")
        if any(
            b.kind == "trigger" and b.trigger_index == body.trigger_index
            for b in release.bindings_for_item(item.item_id)
        ):
            raise InvalidBody("/trigger_index", "trigger_is_bound")
        if body.until.kind == "days" and (body.until.days is None or body.until.days < 1):
            raise InvalidBody("/until/days", "required")
        group = self.manual_members(uow, body.person_id)
        self.manual_capacity(uow, body, group)
        effect = ReplaceCadence(trigger.new_cadence)
        binding = Binding(
            "routine.manual",
            "trigger",
            body.item_id,
            trigger.index,
            trigger.text,
            None,
            None,
            WarningRaisedOn(()),
            effect,
            UntilDays(body.until.days or 1) if body.until.kind == "days" else UntilPersonStops(role),
            "person" if body.person_id else "all_crew",
            trigger.basis,
        )
        firing = Firing(
            binding,
            "person",
            session.actor.id,
            uow.now,
            mission.mission_mode,
            tuple(p for p, _ in group),
            None,
        )
        override = self.engine.inputs.new_override(
            uow,
            firing,
            group,
            mission,
            release,
            self.deps.ids.derived("rov", "manual", session.actor.id, key),
            body.person_id,
            "person",
        )
        return replace(override, started_by="person", binding_id=None, note=body.note)

    def end_override(
        self, session: SessionContext, override_id: str, *, if_match: int | None, idempotency_key: str
    ) -> tuple[int, OverrideView]:
        from crew_health.routine.application.views import override_view

        cmd = identity(session, idempotency_key, "endOverride", override_id, {"override_id": override_id}, {})
        with self.deps.uow.begin(
            actor=session.actor, command=cmd, correlation_id=self.deps.ids.new("cor")
        ) as uow:
            stored = self.deps.idempotency.lookup(uow, cmd)
            override = self.deps.store.get_override(uow, override_id)
            if override is None:
                raise NotFound()
            permitted = (
                override.ends_kind == "person_stops"
                and any(r.value == override.stop_role for r in session.roles)
            ) or (override.awaiting_disposition and Role.CREW_MEDICAL_OFFICER in session.roles)
            if not permitted or session.actor.kind == "assistant":
                raise ActionNotAllowed("stop_role_required")
            if stored is not None:
                if not visible_override(override, session):
                    raise NotFound()
                return stored.status, override_view(override, session)
            revision(override.revision, if_match)
            if override.state == "ended":
                raise InvalidTransition(override.revision)
            changed = self.engine.inputs.end(uow, override, "stopped_by_person", uow.now)
            self.record(uow, cmd, 200, override_id)
            return 200, override_view(changed, session)
