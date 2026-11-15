from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.clock import require_utc
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.routine.application.scans import bounded_pages
from crew_health.routine.domain.cadence import EffectiveCadence
from crew_health.routine.domain.errors import ConfigInvalid, Finding, MissionUnavailable
from crew_health.routine.domain.occurrence import Occurrence, allowed_actions, display_state
from crew_health.routine.domain.override import Override
from crew_health.routine.domain.release import Cadence, RoutineItem, RoutineRelease, RoutineWords
from crew_health.routine.domain.rendering import cadence_text, end_text_key, hhmm, render
from crew_health.routine.domain.vocabulary import FINAL_STATES, OPEN_STATES, OccurrenceState, Overlay

if TYPE_CHECKING:
    from crew_health.routine.application.deps import RoutineDeps
    from crew_health.routine.application.engine import RoutineEngine


def instant(at: datetime | None) -> str | None:
    if at is None:
        return None
    require_utc(at)
    return at.isoformat().replace("+00:00", "Z")


def cadence_view(cadence: Cadence) -> dict[str, object]:
    result: dict[str, object] = {"word": cadence.word, "n": cadence.n}
    for key, value in (("event", cadence.event), ("phase", cadence.phase), ("note", cadence.note)):
        if value is not None:
            result[key] = value
    return result


def require_words(release: RoutineRelease) -> RoutineWords:
    if release.words is None:
        return RoutineWords({}, {}, {}, {}, {}, "ui-copy")
    return release.words


def day_view(deps: RoutineDeps, day: int, version: str) -> dict[str, object]:
    start, _ = deps.mission_clock.day_bounds(day, version=version)
    label = deps.mission_clock.met(start, version=version).day_label
    return {"mission_day": day, "environment_day": None, "label": label, "short_label": label}


def override_visible(override: Override, session: SessionContext) -> bool:
    if session.roles & {Role.CREW_MEDICAL_OFFICER, Role.GROUND}:
        return True
    if override.audience == "person":
        return session.person_id is not None and session.person_id == override.person_id
    return session.person_id in override.member_ids or bool(
        session.roles & frozenset((Role.CREW_MEDICAL_OFFICER, Role.GROUND))
    )


def can_end(override: Override, session: SessionContext) -> bool:
    if override.state != "active":
        return False
    if override.awaiting_disposition and Role.CREW_MEDICAL_OFFICER in session.roles:
        return True
    return override.ends_kind == "person_stops" and any(
        role.value == override.stop_role for role in session.roles
    )


def override_view(override: Override, session: SessionContext) -> dict[str, object]:
    return {
        "override_id": override.override_id,
        "revision": override.revision,
        "item_id": override.item_id,
        "person_id": override.person_id,
        "audience": override.audience,
        "binding_id": override.binding_id,
        "trigger_index": override.trigger_index,
        "trigger_text": override.trigger_text,
        "started_by": override.started_by,
        "source": {"kind": override.source_kind, "ref_id": override.source_ref},
        "new_cadence": cadence_view(override.new_cadence),
        "cadence_text": override.cadence_text,
        "reason_text": override.reason_text,
        "state": override.state,
        "started_at": instant(override.started_at),
        "ends": {"kind": override.ends_kind, "at": instant(override.ends_at), "text": override.ends_text},
        "ended_at": instant(override.ended_at),
        "end_reason": override.end_reason,
        "basis": override.basis,
        "source_ids": list(override.source_ids),
        "config_release_id": override.config_release_id,
        "allowed_actions": ["end"] if can_end(override, session) else [],
    }


def _day_at(deps: RoutineDeps, at: datetime) -> int:
    version = deps.mission_clock.version_at(at).clock_version
    return deps.mission_clock.mission_day(at, version=version)


def _end_text(
    deps: RoutineDeps,
    occ: Occurrence,
    words: RoutineWords,
    now: datetime,
    held: Overlay | None,
    state: OccurrenceState,
) -> str:
    today = _day_at(deps, now)
    done_today = occ.completed_at is not None and _day_at(deps, occ.completed_at) == today
    key = end_text_key(
        state, done_today=done_today, specific=occ.due_kind == "specific", held=held is not None
    )
    at = occ.completed_at if state == "done" else occ.later_until or occ.due_from
    values = {
        "day_label": str(day_view(deps, occ.mission_day, occ.clock_version)["label"]),
        "reason": words.unknown
        if occ.skip is None
        else words.skip_reasons.get(occ.skip.reason, words.unknown),
        "overlay": words.overlay_words.get(held, words.unknown) if held is not None else words.unknown,
    }
    if at is not None:
        values["time"] = hhmm(deps.mission_clock.met(at, version=occ.clock_version).met)
    return render(words.end_text.get(key, words.unknown), values, words.unknown)


def occurrence_view(
    deps: RoutineDeps,
    engine: RoutineEngine,
    view: UnitOfWork | ReadSnapshot,
    occurrence: Occurrence,
    session: SessionContext,
    now: datetime,
) -> dict[str, object]:
    release = engine.release_for(view, occurrence.config_release_id)
    words = require_words(release)
    today, _ = engine.day(now)
    due_until, held = (
        (occurrence.due_until, None)
        if occurrence.state in FINAL_STATES
        else engine.hold(view, occurrence, release, now)
    )
    state, held = display_state(occurrence, now=now, today=today, held_kind=held)
    if occurrence.state == "not_due" and occurrence.due_from is None:
        state, held = "not_due", None
    occ = replace(occurrence, due_until=due_until)
    item = release.item(occ.item_id)
    if item is None:
        raise ConfigInvalid((Finding("error", "/item_id", "Pinned routine item is unavailable"),))
    effective = engine.effective(view, occ.person_id, item, release, occ.environment_id)
    group = release.group_for(effective.cadence.word)
    if (
        occ.state in ("due", "later")
        or state in ("due", "later")
        or (occ.mission_day == today and state != "not_due")
    ):
        group = "today"
    if group is None:
        raise ConfigInvalid((Finding("error", "/display_groups", "Cadence has no display group"),))
    return {
        "occurrence_id": occ.occurrence_id,
        "revision": occ.revision,
        "item_id": occ.item_id,
        "person_id": occ.person_id,
        "environment_id": occ.environment_id,
        "day": day_view(deps, occ.mission_day, occ.clock_version),
        "group": group,
        "state": state,
        "due_from": instant(occ.due_from),
        "due_until": instant(occ.due_until),
        "end_text": _end_text(deps, occ, words, now, held, state),
        **_record_fields(occ),
        "question_set_ids": _question_sets(deps, view, occ, release),
        "suppressed_by_overlay": held,
        "config_release_id": occ.config_release_id,
        "allowed_actions": list(
            allowed_actions(occ, session_person_id=session.person_id, held=held is not None)
        )
        if state in ("due", "later")
        else [],
    }


def _question_sets(
    deps: RoutineDeps, view: UnitOfWork | ReadSnapshot, occ: Occurrence, release: RoutineRelease
) -> list[str]:
    maximum = (
        deps.settings.max_active_overrides_per_person
        * (deps.settings.max_slots_per_binding + 1)
        * deps.settings.history_days
    )
    instances = bounded_pages(
        lambda size, offset: deps.store.instances_for_occurrence(
            view, occ.occurrence_id, size, offset=offset
        ),
        page=deps.settings.page_limit,
        maximum=maximum,
    )
    result = {inst.question_set_id for inst in instances}
    if occ.item_id == release.question_carrier_item_id():
        daily_instances = bounded_pages(
            lambda size, offset: deps.store.instances_for_day(
                view, occ.person_id, occ.mission_day, size, offset=offset
            ),
            page=deps.settings.page_limit,
            maximum=maximum,
        )
        for inst in daily_instances:
            carrier = deps.store.instance_carrier(view, inst.instance_id)
            if carrier is not None and carrier[1] is None:
                result.add(inst.question_set_id)
    return sorted(result)


def _record_fields(occ: Occurrence) -> dict[str, object]:
    return {
        "cause": {
            "kind": occ.cause_kind,
            "override_id": occ.override_id,
            "binding_id": occ.binding_id,
            "started_by": occ.started_by,
            "insight_id": occ.insight_id,
        },
        "completed_at": instant(occ.completed_at),
        "outcome": None
        if occ.outcome is None
        else {
            "observation_ids": list(occ.outcome.observation_ids),
            "answer_ids": list(occ.outcome.answer_ids),
            "note": occ.outcome.note,
        },
        "skip": None
        if occ.skip is None
        else {"reason": occ.skip.reason, "note": occ.skip.note, "at": instant(occ.skip.at)},
    }


def environment(deps: RoutineDeps, view: ReadSnapshot | UnitOfWork) -> str:
    facts = None if deps.mission is None else deps.mission.current(view)
    if facts is None:
        raise MissionUnavailable("mission_facts_required")
    return facts.active_environment_id


def task_view(
    deps: RoutineDeps,
    engine: RoutineEngine,
    view: ReadSnapshot,
    item: RoutineItem,
    session: SessionContext,
    now: datetime,
) -> dict[str, object]:
    release = engine.release(view)
    words = require_words(release)
    env = environment(deps, view)
    person = session.person_id
    if person is None:
        from crew_health.routine.domain.errors import Unauthorized

        raise Unauthorized("person_required")
    effective = engine.effective(view, person, item, release, env)
    overrides = deps.store.active_overrides_for(view, person, deps.settings.max_active_overrides_per_person)
    next_occ = min(
        (
            o
            for o in engine.today_occurrences(view, person, now)
            if o.item_id == item.item_id and o.state in OPEN_STATES
        ),
        key=lambda o: (o.due_from is None, o.due_from or now, o.occurrence_id),
        default=None,
    )
    return {
        "item_id": item.item_id,
        "name": item.name,
        "plain": item.plain,
        "why": item.why,
        "who": list(item.who),
        "instrument": item.instrument,
        "minutes": item.minutes,
        "steps": list(item.steps),
        "body_region": item.body_region,
        "environment_id": env,
        "base_cadence": cadence_view(item.cadence_for(env)),
        "effective_cadence": cadence_view(effective.cadence),
        "cadence_text": cadence_text(effective.cadence, words, words.overlay_words.get("eva")),
        "basis": effective.basis,
        "triggers": _triggers(item, release),
        "active_override_ids": [o.override_id for o in overrides if o.item_id == item.item_id],
        "next_occurrence_id": None if next_occ is None else next_occ.occurrence_id,
        "citation": _citation(deps, engine, view, release, effective),
    }


def _citation(
    deps: RoutineDeps,
    engine: RoutineEngine,
    view: ReadSnapshot,
    release: RoutineRelease,
    effective: EffectiveCadence,
) -> dict[str, object]:
    sources = release.sources
    if effective.governing_override_id is not None:
        governing = deps.store.get_override(view, effective.governing_override_id)
        if governing is not None:
            sources = engine.release_for(view, governing.config_release_id).sources
    text = ", ".join(sources[s] for s in effective.source_ids if s in sources)
    return {
        "text": text or require_words(release).unknown,
        "source_ids": list(effective.source_ids),
        "basis": effective.basis,
    }


def _triggers(item: RoutineItem, release: RoutineRelease) -> list[dict[str, object]]:
    triggers: list[dict[str, object]] = []
    for trigger in item.triggers:
        binding = next(
            (
                b
                for b in release.bindings_for_item(item.item_id)
                if b.kind == "trigger" and b.trigger_index == trigger.index
            ),
            None,
        )
        triggers.append(
            {
                "index": trigger.index,
                "trigger_text": trigger.text,
                "binding_id": None if binding is None else binding.binding_id,
                "mode": "manual" if binding is None else "automatic",
                "new_cadence": cadence_view(trigger.new_cadence),
                "basis": trigger.basis,
            }
        )
    return triggers
