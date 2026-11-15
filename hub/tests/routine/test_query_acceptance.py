"""Crew-visible query acceptance through the registered module and real station."""

import base64
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import Role
from crew_health.contracts_gen.schemas.routine_occurrence_schema import RoutineOccurrence
from crew_health.contracts_gen.schemas.routine_task_schema import RoutineTask
from crew_health.routine.application.ports import OccurrenceActionBody, OverrideCreateBody, OverrideUntil
from crew_health.routine.domain.errors import InvalidBody, NotFound, Unauthorized

from .harness import RoutineHarness, session, station


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def rows(wire: dict[str, object]) -> list[dict[str, object]]:
    value = wire["items"]
    assert isinstance(value, list)
    return cast(list[dict[str, object]], value)


def collect(fetch: Callable[[str | None], dict[str, object]], maximum: int) -> list[dict[str, object]]:
    cursor = None
    result: list[dict[str, object]] = []
    for _ in range(maximum + 1):
        wire = fetch(cursor)
        result.extend(rows(wire))
        paging = cast(dict[str, object], wire["page"])
        total = paging["total"]
        assert isinstance(total, int) and total >= len(result)
        next_cursor = paging["next_cursor"]
        if next_cursor is None:
            assert paging["total"] == len(result)
            return result
        assert isinstance(next_cursor, str) and next_cursor != cursor
        cursor = next_cursor
    raise AssertionError("query cursor did not terminate")


def item_occurrence(hub: RoutineHarness, item: str) -> str:
    return str(
        hub.rows(
            "SELECT occurrence_id FROM routine_occurrence WHERE person_id=? AND item_id=?",
            ("person_mara", item),
        )[0][0]
    )


def row_for(hub: RoutineHarness, item: str) -> dict[str, object]:
    return next(
        row
        for row in rows(hub.queries.occurrences(session(), "today", None, None, 100))
        if row["item_id"] == item
    )


@pytest.mark.trace("T-E25-015", "T-E25-022")
def test_occurrence_collection_pages_preserve_contract_order_and_state_filters(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1, 8))
    done = item_occurrence(hub, "hydration")
    skipped = item_occurrence(hub, "resting_pulse")
    hub.commands.act(
        session(), done, OccurrenceActionBody("complete"), if_match=1, idempotency_key="cmd_query_done"
    )
    hub.commands.act(
        session(),
        skipped,
        OccurrenceActionBody("skip", skip_reason="no_time"),
        if_match=1,
        idempotency_key="cmd_query_skip",
    )
    all_rows = rows(hub.queries.occurrences(session(), "today", None, None, 100))
    paged = collect(lambda c: hub.queries.occurrences(session(), "today", None, c, 3), len(all_rows))
    assert paged == all_rows
    assert len({row["occurrence_id"] for row in paged}) == len(paged)
    rank = {state: n for n, state in enumerate(("due", "later", "done", "skipped", "missed", "not_due"))}
    with hub.db.snapshot() as view:
        position = {item.item_id: n for n, item in enumerate(hub.engine.release(view).items)}
    keys = [
        (
            rank[str(r["state"])],
            str(r["due_from"] or "~"),
            position[str(r["item_id"])],
            str(r["occurrence_id"]),
        )
        for r in paged
    ]
    assert keys == sorted(keys)
    assert any(row["state"] == "done" for row in paged)
    assert any(row["state"] == "skipped" for row in paged)
    for state in ("due", "later", "done", "skipped", "not_due"):
        selected = rows(hub.queries.occurrences(session(), "week", state, None, 100))
        assert selected == [row for row in paged if row["state"] == state]
        for row in selected:
            RoutineOccurrence.model_validate(row)


@pytest.mark.trace("T-E25-016", "T-E25-109")
def test_weekly_row_shows_latest_final_then_next_unseen_window_after_outage(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1, 14))
    identifier = item_occurrence(hub, "vision_self_check")
    hub.commands.act(
        session(), identifier, OccurrenceActionBody("complete"), if_match=1, idempotency_key="cmd_weekly_done"
    )
    hub.advance(at(2, 8))
    latest = row_for(hub, "vision_self_check")
    assert latest["occurrence_id"] == identifier
    assert latest["state"] == "done" and latest["group"] == "week"
    assert latest["allowed_actions"] == []
    hub.advance(at(8, 23))
    projected = row_for(hub, "vision_self_check")
    day = cast(dict[str, object], projected["day"])
    assert day["mission_day"] == 9
    assert projected["state"] == "not_due" and projected["group"] == "week"
    assert projected["allowed_actions"] == []
    assert hub.rows(
        "SELECT count(*) FROM routine_occurrence WHERE person_id=? AND item_id=?",
        ("person_mara", "vision_self_check"),
    ) == ((1,),)


@pytest.mark.trace("T-E25-005", "T-E25-043")
def test_walk_summary_names_no_walk_first_walk_and_subsequent_planned_walk(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(2, 8))
    with hub.db.snapshot() as view:
        words = hub.engine.release(view).words
        assert words is not None
    unplanned = row_for(hub, "post_eva_check")
    assert unplanned["state"] == "not_due" and unplanned["group"] == "walk"
    assert unplanned["end_text"] == words.end_text["no_walk_planned"]
    hub.overlay("ovl_planned_walk", kind="eva", state="planned", planned_start=at(3, 10))
    hub.deliver()
    first = row_for(hub, "post_eva_check")
    assert first["end_text"] == words.end_text["first_walk"].replace("{day_label}", "Day 3")
    hub.overlay(
        "ovl_prior_walk", kind="eva", state="ended", revision=2, started_at=at(1, 7), ended_at=at(1, 8)
    )
    hub.deliver()
    following = row_for(hub, "post_eva_check")
    assert following["end_text"] == words.end_text["next_walk"].replace("{day_label}", "Day 3")
    assert following["allowed_actions"] == []
    RoutineOccurrence.model_validate(following)


@pytest.mark.trace("T-E25-007", "D16")
def test_task_pages_serve_all_configured_content_and_governing_provenance(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1, 8))
    with hub.db.snapshot() as view:
        release = hub.engine.release(view)
    tasks = collect(lambda c: hub.queries.tasks(session(), c, 3), len(release.items))
    assert [task["item_id"] for task in tasks] == [item.item_id for item in release.items]
    for task, item in zip(tasks, release.items, strict=True):
        RoutineTask.model_validate(task)
        assert {
            name: task[name] for name in ("name", "plain", "why", "instrument", "minutes", "body_region")
        } == {
            "name": item.name,
            "plain": item.plain,
            "why": item.why,
            "instrument": item.instrument,
            "minutes": item.minutes,
            "body_region": item.body_region,
        }
        # M5, E25 3.12.2: requirement operator "self"; an item the subject cannot perform alone is unsupported
        # guidance, served as the one-item notice form.
        expected = list(item.steps) if "self" in item.who else [release.notice]
        assert task["steps"] == expected and task["who"] == list(item.who)
    hub.raise_warning()
    hub.deliver()
    changed = hub.queries.task(session(), "vision_self_check")
    trigger = next(item for item in release.items if item.item_id == "vision_self_check").triggers[1]
    assert changed["effective_cadence"] == {"word": "daily", "n": None, "note": trigger.new_cadence.note}
    assert changed["basis"] == trigger.basis
    citation = cast(dict[str, object], changed["citation"])
    assert citation["source_ids"] == list(trigger.source_ids)
    # M5: the configured "Not clinically reviewed" notice rides on the citation line (RoutineTask has no field).
    assert (
        citation["text"] == ", ".join(release.sources[s] for s in trigger.source_ids) + " " + release.notice
    )
    assert changed["active_override_ids"]
    triggers = cast(list[dict[str, object]], changed["triggers"])
    assert triggers[0]["mode"] == "manual" and triggers[0]["binding_id"] is None
    assert (
        triggers[1]["mode"] == "automatic"
        and triggers[1]["binding_id"] == "vision_self_check.co2_above_limit"
    )


@pytest.mark.trace("INV-E25-07", "T-E25-007")
def test_override_paging_filters_membership_and_person_privacy_before_returning_rows(tmp_path: Path) -> None:
    hub = station(tmp_path, at=at(1, 8), values={"routine.page_limit": 1})
    officer = session(roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
    _, private = hub.commands.create_override(
        officer,
        OverrideCreateBody("vision_self_check", 0, "person_tomas", OverrideUntil("person_stops")),
        idempotency_key="cmd_private_override",
    )
    hub.raise_warning()
    hub.deliver()
    groups = {
        str(row[0]) for row in hub.rows("SELECT override_id FROM routine_override WHERE audience<>'person'")
    }
    assert len(groups) == 2

    def identifiers(person: str | None, roles: frozenset[Role] = frozenset({Role.CREW})) -> set[str]:
        values = collect(lambda c: hub.queries.overrides(session(person, roles=roles), c, 1), 10)
        return {str(value["override_id"]) for value in values}

    # M4: starting an override is not a grant; the officer who started Tomas's private override reads it only
    # through a current Visibility grant.
    assert identifiers("person_mara") == groups
    assert identifiers("person_tomas") == groups | {str(private["override_id"])}
    assert identifiers("person_ilya") == set()
    # M4: officer and ground status alone read no group override (per object grants only).
    assert identifiers("person_ilya", frozenset({Role.CREW_MEDICAL_OFFICER})) == set()
    assert identifiers(None, frozenset({Role.GROUND})) == set()


@pytest.mark.trace("INV-E25-07", "T-E25-015")
def test_query_rejections_keep_private_objects_and_invalid_page_requests_closed(tmp_path: Path) -> None:
    hub = station(tmp_path)
    station_reads: tuple[Callable[[], object], ...] = (
        lambda: hub.queries.schedule(session(None), "today"),
        lambda: hub.queries.tasks(session(None), None, 100),
        lambda: hub.queries.overrides(session(None), None, 100),
    )
    for read in station_reads:
        with pytest.raises(Unauthorized):
            read()
    for read in (
        lambda: hub.queries.task(session(), "missing_item"),
        lambda: hub.queries.occurrence(session(), "ro_missing"),
    ):
        with pytest.raises(NotFound):
            read()
    for read, pointer in (
        (lambda: hub.queries.schedule(session(), "month"), "/range"),
        (lambda: hub.queries.occurrences(session(), "today", "unsafe", None, 100), "/state"),
        (lambda: hub.queries.tasks(session(), base64.b64encode(b"foreign:1").decode(), 100), "/cursor"),
        (lambda: hub.queries.tasks(session(), None, 0), "/limit"),
        (lambda: hub.queries.tasks(session(), None, 201), "/limit"),
    ):
        with pytest.raises(InvalidBody) as invalid:
            read()
        assert invalid.value.pointer == pointer
