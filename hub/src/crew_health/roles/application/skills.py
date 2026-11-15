"""Skill records, crew readiness, drills and event practice (E31 design 3.2, 3.6, 4.1; CONTRACTS 24)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from crew_health.contracts_gen.enums import ErrorCode, EventType, Role
from crew_health.kernel.commands import AcceptedReceipt, CommandIdentity, StoredResult, command_identity
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import EventScope, SessionContext, UnitOfWork
from crew_health.roles.application import wire
from crew_health.roles.application.repository import RolesRepository
from crew_health.roles.application.world import (
    CREW,
    Deps,
    World,
    audit,
    call,
    emit,
    label_at,
    practice_origin,
    read_mission,
    read_world,
    record_for,
    register_reference,
)
from crew_health.roles.domain.drill import Drill, abandon_if_ended, complete, core_fresh_at, needed_supplies
from crew_health.roles.domain.errors import (
    DrillNotFound,
    InvalidDrill,
    KeyReused,
    NotAPerson,
    NotRunning,
    NotVisible,
    OriginMismatch,
    PortUnavailable,
    RevisionRequired,
    StaleRevision,
    SuppliesNotAboard,
    TooManyOpenDrills,
)
from crew_health.roles.domain.freshness import Practice
from crew_health.roles.domain.rules import RolesConfig, render

OFFICER_ROLES = frozenset({Role.CREW_MEDICAL_OFFICER, Role.STATION})


@dataclass(frozen=True, slots=True)
class DrillStart:
    scenario_id: str
    skill_ids: tuple[str, ...]
    participant_ids: tuple[str, ...]
    routine_occurrence_id: str | None
    origin_mode: Literal["LIVE", "SIM"]
    key: str
    body: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class DrillFinish:
    drill_id: str
    participant_ids: tuple[str, ...] | None
    if_match: int | None
    key: str
    body: Mapping[str, object]


def _digest(body: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Skills:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    # --------------------------------------------------------------------------------------------------- views
    def my_skills(self, session: SessionContext) -> dict[str, object]:
        """Only the session person's own records (privacy between crew, rule 7)."""
        person_id = session.person_id
        if person_id is None:
            raise NotAPerson(session.session_id)
        world = read_world(self.deps, self.deps.clock.now())
        member = world.member(person_id)
        if member is None:
            raise NotAPerson(session.session_id)
        label = label_at(self.deps)
        with self.deps.uow.begin(actor=session.actor, correlation_id=self.deps.ids.new("cor")) as uow:
            repo = self.deps.repository(uow.connection)
            cfg = self.deps.config_cache().get(uow.release)
            origin = practice_origin(world.mission)
            records = []
            for skill_id in cfg.skill_order:
                record = record_for(repo, cfg, person_id, skill_id, world.mission, world.at)
                last = None if record.last is None else label(record.last.practiced_at)
                records.append(wire.skill_record(record, cfg, member.display_name, origin, last))
            return {
                "person": {"person_id": person_id, "display_name": member.display_name},
                "skills": records,
                "as_of": wire.instant(world.at),
            }

    def readiness(self, session: SessionContext) -> dict[str, object]:
        """crew_medical_officer only: who holds each skill and who is available now; no ranking, roster order."""
        if Role.CREW_MEDICAL_OFFICER not in session.roles:
            raise NotVisible("crew_readiness")
        world = read_world(self.deps, self.deps.clock.now())
        label = label_at(self.deps)
        with self.deps.uow.begin(actor=session.actor, correlation_id=self.deps.ids.new("cor")) as uow:
            repo = self.deps.repository(uow.connection)
            cfg = self.deps.config_cache().get(uow.release)
            subjects = {c.subject_person_id for c in repo.open_contexts(self.deps.settings.page_max)}
            skills = [
                self._readiness_skill(repo, cfg, world, skill_id, subjects, label)
                for skill_id in cfg.skill_order
            ]
            return {
                "environment_id": world.mission.active_environment_id,
                "skills": skills,
                "as_of": wire.instant(world.at),
            }

    def _readiness_skill(
        self,
        repo: RolesRepository,
        cfg: RolesConfig,
        world: World,
        skill_id: str,
        subjects: set[str | None],
        label: Callable[[datetime], str | None],
    ) -> dict[str, object]:
        skill = cfg.skills[skill_id]
        rows: list[dict[str, object]] = []
        fresh = 0
        for person in sorted(world.people, key=lambda p: (p.roster_index, p.person_id)):
            record = record_for(repo, cfg, person.person_id, skill_id, world.mission, world.at)
            fresh += 1 if record.freshness == "fresh" else 0
            reason = (
                "is_subject"
                if person.person_id in subjects
                else "on_eva"
                if person.on_eva
                else "asleep"
                if person.asleep
                else None
            )
            last = None
            if record.last is not None:
                text = label(record.last.practiced_at)
                last = text if text is not None else cfg.words.unknown
            rows.append(
                {
                    "person": {"person_id": person.person_id, "display_name": person.display_name},
                    "freshness": record.freshness,
                    "last_practiced_label": last,
                    "medical_officer_rank": person.officer_rank,
                    "available": reason is None,
                    "unavailable_reason": reason,
                }
            )
        words = cfg.words.readiness_words
        template = words["fresh_count"] if fresh else words["nobody_fresh"]
        text = render(template, {"skill_short": skill.short_name, "n": str(fresh)}, cfg.words.unknown)
        return {"skill_id": skill_id, "name": skill.name, "fresh_count": fresh, "text": text, "holders": rows}

    # -------------------------------------------------------------------------------------------------- drills
    def start_drill(self, session: SessionContext, cmd: DrillStart) -> tuple[int, bytes]:
        world = read_world(self.deps, self.deps.clock.now())
        identity = command_identity(
            key=cmd.key,
            actor_ref=session.actor.id,
            operation="startDrill",
            target="drills",
            parameters_json=b"{}",
            body_fingerprint=_digest(cmd.body),
            fingerprint_kind="sha256",
        )
        with self.deps.uow.begin(
            actor=session.actor, command=identity, correlation_id=self.deps.ids.new("cor")
        ) as uow:
            replay = self._replay(uow, identity, cmd.key, world)
            if replay is not None:
                return replay
            repo = self.deps.repository(uow.connection)
            self._abandon_ended(uow, repo)
            drill = self._new_drill(uow, session, repo, cmd, world)
            body = self._drill_body(drill, world)
            # The receipt names the command's target (the drills collection); the drill is the resource it made.
            receipt = AcceptedReceipt(
                cmd.key, "startDrill", identity.target, self.deps.ids.new("rcp"), uow.now
            )
            self.deps.idempotency.record(
                uow, identity, StoredResult(receipt, 201, drill.drill_id, "accepted", None)
            )
            return 201, body

    def _new_drill(
        self, uow: UnitOfWork, session: SessionContext, repo: RolesRepository, cmd: DrillStart, world: World
    ) -> Drill:
        if not session.roles & OFFICER_ROLES and session.person_id not in cmd.participant_ids:
            raise NotVisible("drill")  # a crew session starts a drill only as a participant
        cfg = self.deps.config_cache().get(uow.release)
        supplies = needed_supplies(cfg.skills, cmd.scenario_id, cmd.skill_ids)
        roster = {m.person_id: m.roster_index for m in world.roster}
        for index, pid in enumerate(cmd.participant_ids):
            if pid not in roster:
                raise InvalidDrill(f"/participant_ids/{index}", "not on the roster")
        if cmd.origin_mode != practice_origin(world.mission):
            raise OriginMismatch(cmd.origin_mode)
        limit = self.deps.settings.max_open_drills
        if len(repo.running_drills(limit + 1)) >= limit:
            raise TooManyOpenDrills(limit)
        if cmd.routine_occurrence_id is not None:
            raise InvalidDrill(
                "/routine_occurrence_id", "routine linking is not available"
            )  # E25 port absent
        self._check_supplies(supplies, world.mission.active_environment_id)
        day_end = call("mission", lambda: self.deps.mission.day_end(uow.now))
        drill = Drill(
            drill_id=self.deps.ids.new("drl"),
            revision=1,
            scenario_id=cmd.scenario_id,
            skill_ids=cmd.skill_ids,
            participant_ids=tuple(sorted(cmd.participant_ids, key=lambda p: (roster[p], p))),
            completed_ids=(),
            routine_occurrence_id=None,
            state="running",
            started_at=uow.now,
            completed_at=None,
            abandoned_at=None,
            origin_mode=cmd.origin_mode,
            mission_id=world.mission.mission_id,
            started_by=session.actor.id,
            day_end=day_end,
            config_release_id=uow.release.release_id,
        )
        repo.insert_drill(drill, environment_id=world.mission.active_environment_id, roster_index=roster)
        register_reference(uow, "drill", drill.drill_id, EventType.DRILL_CHANGED)
        emit(uow, EventType.DRILL_CHANGED, self._drill_wire(drill, world), scope=CREW, ref=drill.drill_id)
        audit(
            uow,
            self.deps,
            "roles.drill_started",
            {"drill_id": drill.drill_id},
            {},
        )
        return drill

    def _check_supplies(self, supplies: Sequence[str], environment_id: str) -> None:
        if not supplies:
            return
        port = self.deps.supplies
        if port is None:
            raise SuppliesNotAboard(tuple(supplies))  # unknown stock is never assumed aboard (fail closed)
        aboard = call("supplies", lambda: port.aboard(supplies, environment_id))
        missing = tuple(s for s in supplies if aboard.get(s) is not True)
        if missing:
            raise SuppliesNotAboard(missing)

    def get_drill(self, session: SessionContext, drill_id: str) -> dict[str, object]:
        world = read_world(self.deps, self.deps.clock.now())
        with self.deps.uow.begin(actor=session.actor, correlation_id=self.deps.ids.new("cor")) as uow:
            repo = self.deps.repository(uow.connection)
            self._abandon_ended(uow, repo)
            drill = repo.get_drill(drill_id)
            if drill is None:
                raise DrillNotFound(drill_id)
            return self._drill_wire(drill, world)

    def complete_drill(self, session: SessionContext, cmd: DrillFinish) -> tuple[int, bytes]:
        world = read_world(self.deps, self.deps.clock.now())
        identity = command_identity(
            key=cmd.key,
            actor_ref=session.actor.id,
            operation="completeDrill",
            target=cmd.drill_id,
            parameters_json=json.dumps({"drill_id": cmd.drill_id}).encode(),
            body_fingerprint=_digest(cmd.body),
            fingerprint_kind="sha256",
        )
        with self.deps.uow.begin(
            actor=session.actor, command=identity, correlation_id=self.deps.ids.new("cor")
        ) as uow:
            replay = self._replay(uow, identity, cmd.key, world)
            if replay is not None:
                return replay
            repo = self.deps.repository(uow.connection)
            self._abandon_ended(uow, repo)
            drill = repo.get_drill(cmd.drill_id)
            if drill is None:
                raise DrillNotFound(cmd.drill_id)
            allowed = session.roles & OFFICER_ROLES or session.person_id in drill.participant_ids
            if not allowed and session.actor.id != drill.started_by:
                raise NotVisible(cmd.drill_id)
            if drill.state != "running":
                raise NotRunning(self._drill_wire(drill, world))
            if cmd.if_match is None:
                raise RevisionRequired(cmd.drill_id)
            if cmd.if_match != drill.revision:
                raise StaleRevision(self._drill_wire(drill, world))
            done = complete(drill, cmd.participant_ids, uow.now)
            self._credit(uow, repo, done, world, session)
            body = self._drill_body(done, world)
            receipt = AcceptedReceipt(
                cmd.key, "completeDrill", done.drill_id, self.deps.ids.new("rcp"), uow.now
            )
            self.deps.idempotency.record(
                uow, identity, StoredResult(receipt, 200, done.drill_id, "accepted", None)
            )
            return 200, body

    def _credit(
        self, uow: UnitOfWork, repo: RolesRepository, done: Drill, world: World, session: SessionContext
    ) -> None:
        cfg = self.deps.config_cache().get(uow.release)
        completed_at = done.completed_at
        if completed_at is None:
            raise NotRunning(self._drill_wire(done, world))
        practices = [
            Practice(
                practice_id=self.deps.ids.new("prc"),
                person_id=pid,
                skill_id=skill_id,
                source="drill",
                source_ref_id=done.drill_id,
                practiced_at=completed_at,
                origin_mode=done.origin_mode,
                mission_id=done.mission_id,
            )
            for pid in done.completed_ids
            for skill_id in done.skill_ids
        ]
        repo.add_practices(
            practices, recorded_by=session.actor.id, release_id=uow.release.release_id, at=uow.now
        )
        core = cfg.practice.core_skill_ids if cfg.practice is not None else ()
        fresh: dict[str, bool] = {}
        for pid in done.completed_ids:
            mine = repo.drill_practices(
                pid, done.mission_id, done.origin_mode, completed_at, self.deps.settings.page_max
            )
            fresh[pid] = core_fresh_at(mine, core, cfg.skills, cfg.calendar, completed_at)
        if not repo.save_drill(done, done.revision - 1, completed_by=session.actor.id, core_fresh=fresh):
            raise StaleRevision(self._drill_wire(done, world))
        emit(uow, EventType.DRILL_CHANGED, self._drill_wire(done, world), scope=CREW, ref=done.drill_id)
        for pid in done.completed_ids:
            for skill_id in done.skill_ids:
                self._practiced(uow, repo, cfg, world, pid, skill_id)
        audit(
            uow,
            self.deps,
            "roles.drill_completed",
            {"drill_id": done.drill_id},
            {},
        )
        self._request_recompute(uow, repo)

    def _practiced(
        self,
        uow: UnitOfWork,
        repo: RolesRepository,
        cfg: RolesConfig,
        world: World,
        person_id: str,
        skill_id: str,
    ) -> None:
        member = world.member(person_id)
        if member is None or skill_id not in cfg.skills:
            return
        record = record_for(repo, cfg, person_id, skill_id, world.mission, uow.now)
        label = None if record.last is None else label_at(self.deps)(record.last.practiced_at)
        payload = wire.skill_record(record, cfg, member.display_name, practice_origin(world.mission), label)
        emit(
            uow,
            EventType.SKILL_PRACTICED,
            payload,
            scope=EventScope("person", person_id, None),
            ref=person_id,
        )
        # CONTRACTS 18: skill.practiced goes to the person, and to crew scope for the crew_medical_officer role only.
        emit(
            uow,
            EventType.SKILL_PRACTICED,
            payload,
            scope=CREW,
            ref=person_id,
            rule="roles.crew_medical_officer",
        )

    def _request_recompute(self, uow: UnitOfWork, repo: RolesRepository) -> None:
        for ctx in repo.open_contexts(self.deps.settings.page_max):
            repo.request_recompute(ctx.kind, ctx.context_id, reason="holders_changed", at=uow.now)

    def _abandon_ended(self, uow: UnitOfWork, repo: RolesRepository) -> None:
        for drill in repo.running_drills(self.deps.settings.max_open_drills):
            ended = abandon_if_ended(drill, uow.now)
            if ended is not None and repo.save_drill(ended, drill.revision, completed_by=None, core_fresh={}):
                audit(uow, self.deps, "roles.drill_abandoned", {"drill_id": drill.drill_id}, {})

    def _replay(
        self, uow: UnitOfWork, identity: CommandIdentity, key: str, world: World
    ) -> tuple[int, bytes] | None:
        """SYSTEM 5.8: the ledger keeps the receipt and the drill it named, not the response body; a replay
        answers with that drill as it stands now."""
        try:
            stored = self.deps.idempotency.lookup(uow, identity)
        except ProblemError as problem:
            if problem.code == ErrorCode.DUPLICATE_COMMAND:
                raise KeyReused(key) from problem
            raise
        if stored is None:
            return None
        drill = (
            None
            if stored.resource_ref is None
            else self.deps.repository(uow.connection).get_drill(stored.resource_ref)
        )
        if drill is None:
            raise DrillNotFound(stored.resource_ref or "")
        return stored.status, self._drill_body(drill, world)

    def _drill_wire(self, drill: Drill, world: World) -> dict[str, object]:
        names = {pid: world.name(pid) or "" for pid in drill.participant_ids}
        if any(not n for n in names.values()):
            raise PortUnavailable("roster")  # a participant off the roster is never shown under an ID
        return wire.drill(drill, names)

    def _drill_body(self, drill: Drill, world: World) -> bytes:
        return json.dumps(self._drill_wire(drill, world), sort_keys=True).encode()

    # ------------------------------------------------------------------------------------------ event practice
    def on_guided_check(self, uow: UnitOfWork, payload: Mapping[str, object]) -> int:
        """E30 X12: a checked guided check with agree or no_reference, outside a drill, in the mission practice
        mode, gives its helper one practice of its skill. Anything else gives none."""
        if payload.get("state") != "checked" or payload.get("result") not in ("agree", "no_reference"):
            return 0
        if payload.get("drill_id") is not None:
            return 0
        check_id, skill_id = payload.get("guided_check_id"), payload.get("skill_id")
        helper = payload.get("helper")
        helper_id = helper.get("person_id") if isinstance(helper, Mapping) else None
        occurred = payload.get("occurred_at")
        if not (isinstance(check_id, str) and isinstance(skill_id, str) and isinstance(helper_id, str)):
            return 0
        if not isinstance(occurred, str):
            return 0
        mission = read_mission(self.deps)
        origin = practice_origin(mission)
        if payload.get("origin_mode") != origin or mission.mission_mode == "REPLAY":
            return 0
        repo = self.deps.repository(uow.connection)
        practice = Practice(
            practice_id=self.deps.ids.new("prc"),
            person_id=helper_id,
            skill_id=skill_id,
            source="event",
            source_ref_id=check_id,
            practiced_at=_parse_instant(occurred),
            origin_mode=origin,
            mission_id=mission.mission_id,
        )
        added = repo.add_practices(
            [practice], recorded_by=uow.actor.id, release_id=uow.release.release_id, at=uow.now
        )
        if added:
            self._request_recompute(uow, repo)
            cfg = self.deps.config_cache().get(uow.release)
            self._practiced(uow, repo, cfg, read_world(self.deps, uow.now), helper_id, skill_id)
        return added


def _parse_instant(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("instant without offset")
    return value.astimezone(UTC)
