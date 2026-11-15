from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.clock import require_utc
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.routine.application.privacy import override_visible as override_visible
from crew_health.routine.application.scans import bounded_pages
from crew_health.routine.domain.cadence import EffectiveCadence
from crew_health.routine.domain.errors import ConfigInvalid, Finding, MissionUnavailable
from crew_health.routine.domain.occurrence import Occurrence, allowed_actions, display_state
from crew_health.routine.domain.override import Override
from crew_health.routine.domain.release import (
    Cadence,
    RoutineItem,
    RoutineRelease,
    RoutineWords,
    content_digest,
)
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
        **_record_fields(occ, own=session.person_id == occ.person_id),
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


def _record_fields(occ: Occurrence, *, own: bool) -> dict[str, object]:
    """Per field (PLAN row 30): the free-text notes go only to the person they belong to. A care read holds an
    object grant only, and Visibility cannot express a field grant, so it fails closed on the notes."""
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
            "note": occ.outcome.note if own else None,
        },
        "skip": None
        if occ.skip is None
        else {"reason": occ.skip.reason, "note": occ.skip.note if own else None, "at": instant(occ.skip.at)},
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
    additive = max(
        (o for o in overrides if o.item_id == item.item_id and o.new_cadence.word == "as_needed"),
        key=lambda o: (o.started_at, o.override_id),
        default=None,
    )
    if additive is not None:
        effective = EffectiveCadence(
            additive.new_cadence, additive.override_id, additive.basis, additive.source_ids
        )
    next_occ = min(
        (
            o
            for o in engine.today_occurrences(view, person, now)
            if o.item_id == item.item_id and o.state in OPEN_STATES
        ),
        key=lambda o: (o.due_from is None, o.due_from or now, o.occurrence_id),
        default=None,
    )
    guide, guide_release = guided(
        engine, view, release, item, open_occurrence(engine, view, person, item.item_id)
    )
    return {
        "item_id": item.item_id,
        "name": item.name,
        "plain": guide.plain,
        "why": guide.why,
        "who": list(guide.who),
        "instrument": guide.instrument,
        "minutes": guide.minutes,
        "steps": guidance_steps(guide_release, guide),
        "body_region": guide.body_region,
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


def open_occurrence(
    engine: RoutineEngine, view: ReadSnapshot, person: str, item_id: str
) -> Occurrence | None:
    return min(
        (o for o in engine.open_occurrences(view, person) if o.item_id == item_id),
        key=lambda o: (o.due_from is None, o.due_from or view.now, o.occurrence_id),
        default=None,
    )


def guided(
    engine: RoutineEngine,
    view: ReadSnapshot,
    release: RoutineRelease,
    item: RoutineItem,
    occ: Occurrence | None,
) -> tuple[RoutineItem, RoutineRelease]:
    """PLAN row 28, E25 3.12.3: open work keeps the guidance it started with. With an open occurrence of the item,
    the guidance parts (those item_content_sha256 covers) come from that occurrence's pinned release; cadence,
    name and provenance stay current. An unreadable pin is CONFIG_INVALID, never the active release's steps."""
    if occ is None or occ.config_release_id == release.release_id:
        return item, release
    pinned = engine.release_for(view, occ.config_release_id)
    return pinned.item(item.item_id) or item, pinned


def guidance_steps(release: RoutineRelease, item: RoutineItem) -> list[str]:
    """PLAN rows 29 and 73 without guidance (A8: routine serves its own configured steps). The steps are served
    only when the item is self-performed (the task view is the subject's own instruction: operator `self`) and
    the served parts hash to the pinned release's item_content_sha256, the exact content open work started with
    (row 28). Otherwise `steps` is the one-item notice form of 3.12.2, and Record it, Not available and Later
    still work. Review status is never a gate: the notice is a label on the citation line."""
    pinned = release.item_content_sha256.get(item.item_id)
    if "self" in item.who and pinned is not None and pinned == content_digest(item):
        return list(item.steps)
    return [release.notice]


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
        # M5 (Q1, D20, PLAN row 29): clinical review is a label, never a gate. RoutineTask has no notice field
        # (additionalProperties false; R-E25-3 asks for one), so the configured routine-rules notice rides on the
        # citation line the hub renders, which every task view and printed card shows.
        "text": " ".join(part for part in (text or require_words(release).unknown, release.notice) if part),
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
