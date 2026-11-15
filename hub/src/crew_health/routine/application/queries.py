from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from crew_health.kernel.uow import ReadSnapshot, SessionContext
from crew_health.routine.application.privacy import audit_care, care_granted, read_override
from crew_health.routine.application.views import (
    day_view,
    environment,
    instant,
    occurrence_view,
    override_view,
    require_words,
    task_view,
)
from crew_health.routine.domain.cadence import next_periodic_day, period_days
from crew_health.routine.domain.errors import ConfigInvalid, Finding, InvalidBody, NotFound, Unauthorized
from crew_health.routine.domain.occurrence import Occurrence
from crew_health.routine.domain.override import Override
from crew_health.routine.domain.release import RoutineItem, RoutineRelease
from crew_health.routine.domain.rendering import render
from crew_health.routine.domain.vocabulary import OPEN_STATES
from crew_health.routine.domain.windows import policy_window

if TYPE_CHECKING:
    from crew_health.routine.application.deps import RoutineDeps
    from crew_health.routine.application.engine import RoutineEngine


def person_id(session: SessionContext) -> str:
    if session.person_id is None:
        raise Unauthorized("person_required")
    return session.person_id


def cursor_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        decoded = base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True).decode("ascii")
        if not decoded.startswith("routine:") or not decoded[8:].isdigit():
            raise ValueError
        value = int(decoded[8:])
    except (ValueError, UnicodeError, binascii.Error) as error:
        raise InvalidBody("/cursor", "invalid_cursor") from error
    if value < 0:
        raise InvalidBody("/cursor", "invalid_cursor")
    return value


def page(
    items: Sequence[dict[str, object]], cursor: str | None, limit: int, maximum: int
) -> dict[str, object]:
    if not 1 <= limit <= maximum:
        raise InvalidBody("/limit", "outside_page_bound")
    offset = cursor_offset(cursor)
    end = offset + limit
    next_cursor = None
    if end < len(items):
        next_cursor = base64.urlsafe_b64encode(f"routine:{end}".encode("ascii")).decode("ascii")
    return {"items": list(items[offset:end]), "page": {"next_cursor": next_cursor, "total": len(items)}}


def occurrence_order(occ: dict[str, object], release: RoutineRelease) -> tuple[int, str, int, str]:
    rank = ("due", "later", "done", "skipped", "missed", "not_due")
    item = release.item(str(occ["item_id"]))
    return (
        rank.index(str(occ["state"])),
        str(occ["due_from"] or "~"),
        len(release.items) if item is None else item.position,
        str(occ["occurrence_id"]),
    )


class RoutineQueryService:
    def __init__(self, deps: RoutineDeps, engine: RoutineEngine) -> None:
        self.deps, self.engine = deps, engine

    def active_release_id(self) -> str:
        with self.deps.snapshots.snapshot() as view:
            return view.release.release_id

    def schedule(self, session: SessionContext, range_: str) -> dict[str, object]:
        person = person_id(session)
        self._range(range_)
        with self.deps.snapshots.snapshot() as view:
            release = self.engine.release(view)
            words = require_words(release)
            today, version = self.engine.day(view.now)
            rows = self._rows(view, session, view.now)
            overrides = self.deps.store.active_overrides_for(
                view, person, self.deps.settings.max_active_overrides_per_person
            )
            summary = self.engine.miss_summary(view, person, release, view.now)
            groups: list[dict[str, object]] = []
            if range_ == "today":
                for group_id, _ in release.display_groups:
                    group_rows = [row for row in rows if row["group"] == group_id]
                    if group_rows:
                        groups.append(
                            {
                                "group_id": group_id,
                                "label": words.group_labels.get(group_id, group_id),
                                "occurrences": group_rows,
                            }
                        )
            counts = {
                state: sum(row["group"] == "today" and row["state"] == state for row in rows)
                for state in ("due", "later", "done")
            }
            counts["open_today"] = counts["due"] + counts["later"]
            return {
                "person_id": person,
                "environment_id": environment(self.deps, view),
                "range": range_,
                "day": day_view(self.deps, today, version),
                "as_of": instant(view.now),
                "groups": groups,
                "counts": counts,
                "days": self._days(view, person, today, version, release),
                "active_overrides": [self._override(view, o, session) for o in overrides],
                "misses": {
                    "count": summary.count,
                    "window_days": summary.window_days,
                    "barrier_question_due": summary.barrier_question_due,
                },
                "config_release_id": release.release_id,
            }

    def occurrences(
        self, session: SessionContext, range_: str, state: str | None, cursor: str | None, limit: int
    ) -> dict[str, object]:
        person_id(session)
        self._range(range_)
        if state is not None and state not in ("not_due", "due", "later", "done", "skipped", "missed"):
            raise InvalidBody("/state", "invalid_state")
        with self.deps.snapshots.snapshot() as view:
            rows = self._rows(view, session, view.now)
            if state is not None:
                rows = [row for row in rows if row["state"] == state]
            return page(rows, cursor, limit, 200)

    def occurrence(self, session: SessionContext, occurrence_id: str) -> dict[str, object]:
        person = person_id(session)
        with self.deps.snapshots.snapshot() as view:
            occ = self.deps.store.get(view, occurrence_id)
            if occ is None:
                occ = next(
                    (
                        o
                        for o in self.engine.today_occurrences(view, person, view.now)
                        if o.occurrence_id == occurrence_id
                    ),
                    None,
                )
            if occ is None:
                projected = next(
                    (r for r in self._rows(view, session, view.now) if r["occurrence_id"] == occurrence_id),
                    None,
                )
                if projected is not None:
                    return projected
            if occ is None:
                raise NotFound()
            if occ.person_id != person:
                if not care_granted(session, occ.occurrence_id, self.deps.visibility):
                    raise NotFound()
                audit_care(self.deps, session)
            return occurrence_view(self.deps, self.engine, view, occ, session, view.now)

    def tasks(self, session: SessionContext, cursor: str | None, limit: int) -> dict[str, object]:
        person_id(session)
        with self.deps.snapshots.snapshot() as view:
            release = self.engine.release(view)
            rows = [
                task_view(self.deps, self.engine, view, item, session, view.now) for item in release.items
            ]
            return page(rows, cursor, limit, 200)

    def task(self, session: SessionContext, item_id: str) -> dict[str, object]:
        person_id(session)
        with self.deps.snapshots.snapshot() as view:
            item = self.engine.release(view).item(item_id)
            if item is None:
                raise NotFound()
            return task_view(self.deps, self.engine, view, item, session, view.now)

    def overrides(self, session: SessionContext, cursor: str | None, limit: int) -> dict[str, object]:
        if session.person_id is None and not any(
            role.value in ("ground", "crew_medical_officer") for role in session.roles
        ):
            raise Unauthorized("person_or_authority_required")
        with self.deps.snapshots.snapshot() as view:
            require_words(self.engine.release(view))
            settings = self.deps.settings
            maximum = settings.max_active_overrides_per_person * settings.max_roster
            found: list[dict[str, object]] = []
            for offset in range(0, maximum + settings.page_limit, settings.page_limit):
                batch = self.deps.store.active_overrides(view, settings.page_limit, offset=offset)
                found.extend(
                    self._override(view, o, session) for o in batch if read_override(self.deps, o, session)
                )
                if len(batch) < settings.page_limit:
                    return page(found, cursor, limit, 200)
            raise ConfigInvalid((Finding("error", "/overrides", "Routine override query capacity exceeded"),))

    def _override(self, view: ReadSnapshot, override: Override, session: SessionContext) -> dict[str, object]:
        require_words(self.engine.release_for(view, override.config_release_id))
        return override_view(override, session)

    @staticmethod
    def _range(range_: str) -> None:
        if range_ not in ("today", "week"):
            raise InvalidBody("/range", "invalid_range")

    def _history(self, view: ReadSnapshot, person: str, first: int, last: int) -> tuple[Occurrence, ...]:
        settings = self.deps.settings
        maximum = settings.max_open_per_person * settings.history_days
        result: list[Occurrence] = []
        for offset in range(0, maximum + settings.page_limit, settings.page_limit):
            batch = self.deps.store.for_person_days(
                view, person, first, last, settings.page_limit, offset=offset
            )
            result.extend(batch)
            if len(batch) < settings.page_limit:
                return tuple(result)
        raise ConfigInvalid((Finding("error", "/history", "Routine history query capacity exceeded"),))

    def _rows(self, view: ReadSnapshot, session: SessionContext, now: datetime) -> list[dict[str, object]]:
        person = person_id(session)
        release = self.engine.release(view)
        require_words(release)
        today, _ = self.engine.day(now)
        today_rows = self.engine.today_occurrences(view, person, now)
        result = {
            o.occurrence_id: occurrence_view(self.deps, self.engine, view, o, session, now)
            for o in today_rows
        }
        history = self._history(view, person, max(1, today - self.deps.settings.history_days + 1), today)
        for item in release.items:
            if any(
                o.item_id == item.item_id and (o.state in OPEN_STATES or o.mission_day == today)
                for o in today_rows
            ):
                continue
            self._item_row(view, session, now, release, item, history, result)
        return sorted(result.values(), key=lambda row: occurrence_order(row, release))

    def _item_row(
        self,
        view: ReadSnapshot,
        session: SessionContext,
        now: datetime,
        release: RoutineRelease,
        item: RoutineItem,
        history: tuple[Occurrence, ...],
        result: dict[str, dict[str, object]],
    ) -> None:
        person = person_id(session)
        env = environment(self.deps, view)
        today, version = self.engine.day(now)
        effective = self.engine.effective(view, person, item, release, env)
        period = period_days(effective.cadence, release.calendar)
        latest = max(
            (o for o in history if o.item_id == item.item_id and o.periodic),
            key=lambda o: (o.mission_day, o.occurrence_id),
            default=None,
        )
        if period is not None:
            if latest is not None and (latest.mission_day - 1) // period == (today - 1) // period:
                result[latest.occurrence_id] = occurrence_view(
                    self.deps, self.engine, view, latest, session, now
                )
                return
            day = next_periodic_day(
                period=period,
                anchor_day=1,
                last_final_day=self.deps.store.latest_periodic_final_day(view, person, item.item_id),
                today=today,
                today_has_periodic=False,
            )
            projected = self._projection(view, person, item, day, version)
            until, _ = self.engine.hold(view, projected, release, now)
            if until is not None and until <= now:
                projected = self._projection(view, person, item, day + 1, version)
            wire = occurrence_view(self.deps, self.engine, view, projected, session, now)
            result[projected.occurrence_id] = wire
        elif effective.cadence.word == "per_eva":
            projected = self._projection(view, person, item, today, version, walk=True)
            wire = occurrence_view(self.deps, self.engine, view, projected, session, now)
            wire["end_text"] = self._walk_text(view, person, release, now)
            result[projected.occurrence_id] = wire

    def _walk_text(self, view: ReadSnapshot, person: str, release: RoutineRelease, now: datetime) -> str:
        words = require_words(release)
        today, version = self.engine.day(now)
        start = self.deps.mission_clock.day_bounds(
            max(1, today - self.deps.settings.history_days + 1), version=version
        )[0]
        overlays = self.deps.store.overlays_for(view, person, start, self.deps.settings.max_overlay_intervals)
        planned = [
            o
            for o in overlays
            if o.kind == "eva"
            and o.state == "planned"
            and o.planned_start is not None
            and o.planned_start >= now
        ]
        next_walk = min(planned, key=lambda o: (o.planned_start or now, o.overlay_id), default=None)
        if next_walk is None or next_walk.planned_start is None:
            return words.end_text.get("no_walk_planned", words.unknown)
        day, clock_version = self.engine.day(next_walk.planned_start)
        label = str(day_view(self.deps, day, clock_version)["label"])
        key = (
            "next_walk"
            if any(o.kind == "eva" and o.started_at is not None for o in overlays)
            else "first_walk"
        )
        return render(words.end_text.get(key, words.unknown), {"day_label": label}, words.unknown)

    def _projection(
        self,
        view: ReadSnapshot,
        person: str,
        item: RoutineItem,
        day: int,
        version: str,
        *,
        walk: bool = False,
    ) -> Occurrence:
        release = self.engine.release(view)
        start, end = self.deps.mission_clock.day_bounds(day, version=version)
        window = None if walk else policy_window(release.policy(item.item_id), start, end)
        facts = None if self.deps.mission is None else self.deps.mission.current(view)
        if facts is None:
            from crew_health.routine.domain.errors import MissionUnavailable

            raise MissionUnavailable("mission_facts_required")
        return Occurrence(
            occurrence_id=self.deps.ids.derived("ro", person, item.item_id, str(day), "cadence"),
            revision=1,
            person_id=person,
            item_id=item.item_id,
            environment_id=facts.active_environment_id,
            mission_day=day,
            cause_kind="per_eva" if walk else "cadence",
            cause_key="per_eva" if walk else "cadence",
            periodic=not walk,
            override_id=None,
            binding_id=None,
            started_by=None,
            insight_id=None,
            state="not_due",
            due_from=None if window is None else window.due_from,
            base_due_until=None if window is None else window.due_until,
            due_until=None if window is None else window.due_until,
            due_kind="all_day" if window is None else window.kind,
            later_until=None,
            completed_at=None,
            outcome=None,
            skip=None,
            miss_counted=None,
            end_reason=None,
            guidance_release_id=None,
            config_release_id=release.release_id,
            origin_mode=facts.mission_mode,
            clock_version=version,
            created_at=start,
        )

    def _days(
        self, view: ReadSnapshot, person: str, today: int, version: str, release: RoutineRelease
    ) -> list[dict[str, object]]:
        first = max(1, today - min(release.calendar.week_days, self.deps.settings.history_days) + 1)
        history = self._history(view, person, first, today)
        result: list[dict[str, object]] = []
        for day in range(first, today + 1):
            rows = [o for o in history if o.mission_day == day and o.state != "not_due"]
            result.append(
                {
                    "mission_day": day,
                    "label": day_view(self.deps, day, version)["short_label"],
                    "done": len({o.item_id for o in rows if o.state == "done"}),
                    "due": len({o.item_id for o in rows}),
                }
            )
        return result
