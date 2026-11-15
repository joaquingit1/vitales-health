from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.uow import Actor

from .harness import RoutineHarness, configured_release, station


def revised_rules(hub: RoutineHarness, change: Callable[[dict[str, object]], None]) -> None:
    import json

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
    rules = cast(dict[str, object], json.loads(documents[ConfigName.ROUTINE_RULES].raw))
    change(rules)
    raw = canonical_json(rules)
    documents[ConfigName.ROUTINE_RULES] = ConfigDocumentView(
        ConfigName.ROUTINE_RULES, sha256(raw).hexdigest(), raw
    )
    release = MemoryRelease("rel_review", documents)
    hub.release = release
    hub.db.set_release_provider(lambda: release)


@pytest.mark.trace("D16", "T-E25-007")
def test_repeat_question_instance_expires_with_its_carrier_policy_window(tmp_path: Path) -> None:
    hub = station(tmp_path)

    def short_window(rules: dict[str, object]) -> None:
        policies = cast(list[dict[str, object]], rules["item_policies"])
        for policy in policies:
            if policy["item_id"] == "daily_check":
                policy["window_minutes"] = 30

    revised_rules(hub, short_window)
    hub.raise_warning()
    hub.deliver()
    row = hub.rows(
        "SELECT q.instance_id,q.due_until,o.due_until FROM routine_question_instance q "
        "JOIN routine_occurrence o ON q.carrier_occurrence_id=o.occurrence_id "
        "WHERE q.person_id=? AND q.slot=?",
        ("person_mara", "repeat:0"),
    )[0]
    assert row[1] == row[2]
    assert isinstance(row[2], str)
    hub.advance(datetime.fromisoformat(row[2]))
    hub.dispatch()
    assert hub.rows("SELECT state FROM routine_question_instance WHERE instance_id=?", (row[0],)) == (
        ("unanswered",),
    )


@pytest.mark.trace("T-E25-093", "INV-E25-11")
def test_a_correction_without_its_sweep_cannot_clear_a_clock_regression(tmp_path: Path) -> None:
    hub = station(tmp_path)
    hub.advance(hub.clock.now() + timedelta(hours=2))
    hub.regress(hub.clock.now() - timedelta(seconds=1))
    hub.correct(1000)
    assert not hub.trusted()
    with hub.db.begin(actor=Actor("engine", "engine_routine"), correlation_id="cor_frozen") as uow:
        assert hub.engine.counted(uow, uow.now) is False


@pytest.mark.trace("T-E25-010", "INV-E25-11")
def test_assistant_separate_capacity_cannot_hide_an_existing_routine_occurrence(tmp_path: Path) -> None:
    from .harness import session

    hub = station(tmp_path, values={"routine.max_open_per_person": 1})
    actual = hub.rows(
        "SELECT occurrence_id FROM routine_occurrence WHERE person_id=? AND item_id=?",
        ("person_mara", "daily_check"),
    )
    assert len(actual) == 1
    with hub.db.begin(actor=Actor("assistant", "assistant_1"), correlation_id="cor_recheck") as uow:
        hub.runtime.add_recheck(
            uow,
            person_id="person_mara",
            item_id="vision_self_check",
            insight_id="insight_review",
            due_from=uow.now,
        )
    rows = hub.queries.occurrences(session(), "today", None, None, 100)["items"]
    assert isinstance(rows, list)
    assert str(actual[0][0]) in {str(row["occurrence_id"]) for row in rows}
    hub.advance(hub.clock.now() + timedelta(hours=7))
    due = hub.runtime.due_today("person_mara", hub.clock.now())
    assert str(actual[0][0]) in {occ.occurrence_id for occ in due}
