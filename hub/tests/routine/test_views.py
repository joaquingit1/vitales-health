from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import Role
from crew_health.contracts_gen.schemas.routine_override_schema import RoutineOverride
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.routine.application.views import cadence_view, instant, override_view, require_words
from crew_health.routine.domain.errors import ConfigInvalid
from crew_health.routine.domain.release import Cadence

from .support import NOW, override, release


def session(person_id: str = "p_test", *, officer: bool = False) -> SessionContext:
    roles = frozenset({Role.CREW_MEDICAL_OFFICER}) if officer else frozenset({Role.CREW})
    return SessionContext("ses_test", "crew", Actor("person", person_id), person_id, roles)


@pytest.mark.trace("T-E25-002")
def test_override_wire_is_closed_and_actions_follow_current_role() -> None:
    value = replace(override(), ends_kind="person_stops", stop_role="crew_medical_officer")
    wire = override_view(value, session(officer=True))
    RoutineOverride.model_validate(wire)
    assert wire["allowed_actions"] == ["end"]
    assert wire["source"] == {"kind": "warning", "ref_id": "wrn_1"}
    assert wire["started_at"] == "2026-11-14T00:00:00Z"
    assert override_view(value, session())["allowed_actions"] == []
    ended = replace(value, state="ended", ended_at=NOW, end_reason="stopped_by_person")
    assert override_view(ended, session(officer=True))["allowed_actions"] == []


@pytest.mark.trace("T-E25-002")
def test_cadence_omits_absent_optional_fields_and_instants_use_utc_z() -> None:
    assert cadence_view(Cadence("daily", None, None, None, None)) == {"word": "daily", "n": None}
    assert instant(None) is None
    assert instant(NOW + timedelta(minutes=1)) == "2026-11-14T00:01:00Z"


@pytest.mark.trace("INV-E25-11")
def test_missing_presentation_words_fail_closed() -> None:
    with pytest.raises(ConfigInvalid):
        require_words(replace(release(), words=None))


@pytest.mark.trace("T-E25-015")
def test_pagination_traverses_every_item_once_and_rejects_bad_input() -> None:
    from crew_health.routine.application.queries import page
    from crew_health.routine.domain.errors import InvalidBody

    rows: list[dict[str, object]] = [{"item_id": item.item_id} for item in release().items]
    first = page(rows, None, 2, 2)
    paging = first["page"]
    assert isinstance(paging, dict)
    cursor = paging["next_cursor"]
    assert isinstance(cursor, str)
    second = page(rows, cursor, 2, 2)
    assert first["items"] == rows[:2]
    assert second["items"] == rows[2:4]
    assert paging["total"] == len(rows)
    with pytest.raises(InvalidBody) as bad:
        page(rows, "not-a-valid-cursor", 2, 2)
    assert bad.value.pointer == "/cursor"
    with pytest.raises(InvalidBody) as bound:
        page(rows, None, 3, 2)
    assert bound.value.pointer == "/limit"


@pytest.mark.trace("INV-E25-01")
def test_person_override_privacy_does_not_expand_with_officer_role() -> None:
    from crew_health.routine.application.views import override_visible

    value = replace(override(), audience="person", person_id="p_test")
    assert override_visible(value, session())
    assert not override_visible(value, session("p_other", officer=True))
    group = replace(value, audience="all_crew", person_id=None)
    assert override_visible(group, session("p_other", officer=True))
    assert not override_visible(group, session("p_other"))


@pytest.mark.trace("T-E25-015", "T-E25-022")
def test_real_station_queries_match_wire_schemas_and_read_without_writes(tmp_path: Path) -> None:
    from crew_health.contracts_gen.schemas.routine_occurrence_schema import RoutineOccurrence
    from crew_health.contracts_gen.schemas.routine_schedule_schema import RoutineSchedule
    from crew_health.contracts_gen.schemas.routine_task_schema import RoutineTask
    from crew_health.routine.domain.errors import NotFound, Unauthorized

    from .harness import session as crew_session
    from .harness import station

    runtime = station(tmp_path)
    queries = runtime.queries
    before = runtime.rows("SELECT count(*) FROM routine_occurrence")
    wire = queries.schedule(crew_session(), "today")
    RoutineSchedule.model_validate(wire)
    weekly = queries.schedule(crew_session(), "week")
    RoutineSchedule.model_validate(weekly)
    assert weekly["groups"] == []
    tasks = queries.tasks(crew_session(), None, 100)["items"]
    assert isinstance(tasks, list) and tasks
    for task in tasks:
        RoutineTask.model_validate(task)
    occurrences = queries.occurrences(crew_session(), "today", None, None, 100)["items"]
    assert isinstance(occurrences, list) and occurrences
    for row in occurrences:
        RoutineOccurrence.model_validate(row)
    occurrence_id = str(occurrences[0]["occurrence_id"])
    with pytest.raises(NotFound):
        queries.occurrence(crew_session("person_tomas"), occurrence_id)
    with pytest.raises(Unauthorized):
        queries.schedule(crew_session(None), "today")
    assert runtime.rows("SELECT count(*) FROM routine_occurrence") == before


@pytest.mark.trace("T-E25-015", "INV-E25-13")
def test_new_release_changes_task_content_while_open_occurrences_keep_their_pin(tmp_path: Path) -> None:
    import json
    from hashlib import sha256

    from crew_health.contracts_gen.enums import ConfigName
    from crew_health.kernel.commands import canonical_json
    from crew_health.kernel.config import ConfigDocumentView
    from crew_health.kernel.testing import MemoryRelease

    from .harness import configured_release, station
    from .harness import session as crew_session

    runtime = station(tmp_path)
    queries = runtime.queries
    before = queries.task(crew_session(), "daily_check")
    result = queries.occurrences(crew_session(), "today", None, None, 100)["items"]
    assert isinstance(result, list)
    occurrence_id = next(str(o["occurrence_id"]) for o in result if o["item_id"] == "daily_check")
    original = configured_release()
    documents: dict[ConfigName, ConfigDocumentView] = {
        name: original.document(name)
        for name in ConfigName
        if name
        in (
            ConfigName.ROUTINE_SCHEDULE,
            ConfigName.ROUTINE_RULES,
            ConfigName.UI_COPY,
            ConfigName.QUESTIONNAIRES,
        )
    }
    schedule = json.loads(documents[ConfigName.ROUTINE_SCHEDULE].raw)
    schedule["items"][0]["name"] = "Changed configuration label"
    raw = canonical_json(schedule)
    documents[ConfigName.ROUTINE_SCHEDULE] = ConfigDocumentView(
        ConfigName.ROUTINE_SCHEDULE, sha256(raw).hexdigest(), raw
    )
    new_release = MemoryRelease("rel_next", documents)
    runtime.db.set_release_provider(lambda: new_release)
    after = queries.task(crew_session(), "daily_check")
    assert before["name"] != after["name"]
    assert after["name"] == "Changed configuration label"
    assert queries.occurrence(crew_session(), occurrence_id)["config_release_id"] == "rel_test"


@pytest.mark.trace("T-E25-014", "INV-E25-11")
def test_overdue_query_projects_miss_without_writing_a_transition(tmp_path: Path) -> None:
    from datetime import datetime

    from crew_health.contracts_gen.schemas.routine_occurrence_schema import RoutineOccurrence

    from .harness import session as crew_session
    from .harness import station

    runtime = station(tmp_path)
    rows = runtime.rows(
        "SELECT occurrence_id, due_until, state FROM routine_occurrence WHERE person_id=? AND item_id=?",
        ("person_mara", "daily_check"),
    )
    assert rows and isinstance(rows[0][1], str)
    occurrence_id = str(rows[0][0])
    before = runtime.rows("SELECT count(*) FROM routine_occurrence_transition")
    runtime.advance(datetime.fromisoformat(rows[0][1]))
    wire = runtime.queries.occurrence(crew_session(), occurrence_id)
    RoutineOccurrence.model_validate(wire)
    assert wire["state"] == "missed"
    assert wire["end_text"] == "missed"
    assert wire["allowed_actions"] == []
    assert runtime.rows("SELECT count(*) FROM routine_occurrence_transition") == before
    persisted = runtime.rows(
        "SELECT state, miss_counted FROM routine_occurrence WHERE occurrence_id=?", (occurrence_id,)
    )
    assert persisted == ((rows[0][2], None),)


@pytest.mark.trace("T-E25-007", "D16")
def test_governing_override_keeps_citation_titles_from_its_pinned_release(tmp_path: Path) -> None:
    import json
    from hashlib import sha256

    from crew_health.contracts_gen.enums import ConfigName
    from crew_health.kernel.commands import canonical_json
    from crew_health.kernel.config import ConfigDocumentView
    from crew_health.kernel.testing import MemoryRelease

    from .harness import configured_release, station
    from .harness import session as crew_session

    runtime = station(tmp_path)
    runtime.raise_warning()
    runtime.deliver()
    before = runtime.queries.task(crew_session(), "vision_self_check")
    original = configured_release()
    documents: dict[ConfigName, ConfigDocumentView] = {
        name: original.document(name)
        for name in (
            ConfigName.ROUTINE_SCHEDULE,
            ConfigName.ROUTINE_RULES,
            ConfigName.UI_COPY,
            ConfigName.QUESTIONNAIRES,
        )
    }
    schedule = json.loads(documents[ConfigName.ROUTINE_SCHEDULE].raw)
    for source in schedule["sources"]:
        source["title"] = "Replacement title " + source["id"]
    raw = canonical_json(schedule)
    documents[ConfigName.ROUTINE_SCHEDULE] = ConfigDocumentView(
        ConfigName.ROUTINE_SCHEDULE, sha256(raw).hexdigest(), raw
    )
    next_release = MemoryRelease("rel_next", documents)
    runtime.db.set_release_provider(lambda: next_release)
    after = runtime.queries.task(crew_session(), "vision_self_check")
    assert after["citation"] == before["citation"]
    assert after["basis"] == before["basis"]


@pytest.mark.trace("T-E25-014", "INV-E25-11")
def test_prior_day_open_weekly_occurrence_remains_in_today_group_when_timer_lags(tmp_path: Path) -> None:
    from datetime import datetime

    from .harness import session as crew_session
    from .harness import station

    runtime = station(tmp_path)
    row = runtime.rows(
        "SELECT occurrence_id,due_until FROM routine_occurrence WHERE person_id=? AND item_id=?",
        ("person_mara", "vision_self_check"),
    )[0]
    assert isinstance(row[1], str)
    runtime.advance(datetime.fromisoformat(row[1]) + timedelta(days=1))
    wire = runtime.queries.occurrence(crew_session(), str(row[0]))
    assert wire["state"] == "missed"
    assert wire["group"] == "today"


@pytest.mark.trace("T-E25-007", "D16")
def test_daily_check_occurrence_lists_question_sets_on_its_daily_carrier(tmp_path: Path) -> None:
    from .harness import session as crew_session
    from .harness import station

    runtime = station(tmp_path)
    runtime.raise_warning()
    runtime.deliver()
    rows = runtime.rows(
        "SELECT occurrence_id FROM routine_occurrence WHERE person_id=? AND item_id=?",
        ("person_mara", "daily_check"),
    )
    assert rows
    expected = runtime.rows(
        "SELECT DISTINCT question_set_id FROM routine_question_instance "
        "WHERE person_id=? AND carrier_day=? AND carrier_occurrence_id IS NULL "
        "ORDER BY question_set_id",
        ("person_mara", 1),
    )
    assert expected
    wire = runtime.queries.occurrence(crew_session(), str(rows[0][0]))
    assert wire["question_set_ids"] == [row[0] for row in expected]
