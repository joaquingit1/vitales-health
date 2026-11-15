from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, ConfigName
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.uow import Actor
from crew_health.platform.testing.sqlite import database
from crew_health.routine.adapters.config_reader import ReleaseCache, RoutineReleaseCheck, parse_documents
from crew_health.routine.adapters.sqlite_repository import SqliteRoutineStore, migration_sql
from crew_health.routine.domain.errors import ConfigInvalid, StaleVersion
from crew_health.routine.domain.loader_checks import Bounds
from crew_health.routine.domain.occurrence import miss

from .support import NOW, REPO, occurrence, override


def handle(release_id: str = "rel_test") -> MemoryRelease:
    documents = {}
    for name in (
        ConfigName.ROUTINE_SCHEDULE,
        ConfigName.ROUTINE_RULES,
        ConfigName.UI_COPY,
        ConfigName.QUESTIONNAIRES,
    ):
        path = REPO / "config" / f"{name.value}.json"
        if name in (ConfigName.UI_COPY, ConfigName.QUESTIONNAIRES):
            import json

            raw = json.dumps(
                json.loads(
                    (REPO / "contracts/api/examples/config" / f"{name.value}.example.json").read_text(
                        encoding="utf-8"
                    )
                )["value"]
            ).encode()
        else:
            raw = path.read_bytes()
        documents[name] = ConfigDocumentView(name, sha256(raw).hexdigest(), raw)
    return MemoryRelease(release_id, documents)


@pytest.mark.trace("T-E25-002", "INV-E25-01")
def test_storage_roundtrip_conflicts_and_append_only(tmp_path: Path) -> None:
    db = database(tmp_path)
    db.migrate(migration_sql())
    store = SqliteRoutineStore()
    occ = occurrence()
    with db.begin(actor=Actor("engine", "engine"), correlation_id="cor_1") as uow:
        assert store.insert(uow, occ)
        assert not store.insert(uow, replace(occ, occurrence_id="ro_other"))
        assert store.get(uow, occ.occurrence_id) == occ
        missed, transition = miss(occ, now=NOW, counted=False, reason="deadline", actor_id="engine")
        store.update(uow, missed, expected_revision=1)
        store.append_transition(uow, transition)
        with pytest.raises(StaleVersion):
            store.update(uow, missed, expected_revision=1)
    with db.snapshot() as view:
        assert store.get(view, occ.occurrence_id) == missed
        assert store.latest_periodic_final_day(view, occ.person_id, occ.item_id) == 1
    for statement in (
        "UPDATE routine_occurrence SET revision=revision+1",
        "DELETE FROM routine_occurrence",
        "UPDATE routine_occurrence_transition SET reason_code='changed'",
        "DELETE FROM routine_occurrence_transition",
    ):
        with (
            pytest.raises(StorageUnavailable),
            db.begin(actor=Actor("engine", "engine"), correlation_id="cor_2") as uow,
        ):
            uow.connection.execute(statement)


@pytest.mark.trace("T-E25-022", "T-E25-091")
def test_override_roundtrip_and_overlay_order(tmp_path: Path) -> None:
    from crew_health.routine.domain.bindings import OverlayInput

    db = database(tmp_path)
    db.migrate(migration_sql())
    store = SqliteRoutineStore()
    ovr = override()
    overlay = OverlayInput("ovl_1", 2, "p_test", "sleep", "ended", None, NOW, NOW + timedelta(hours=1), None)
    with db.begin(actor=Actor("engine", "engine"), correlation_id="cor_1") as uow:
        assert store.insert_override(uow, ovr)
        assert not store.insert_override(uow, ovr)
        assert store.get_override(uow, ovr.override_id) == ovr
        assert store.active_overrides_for(uow, "p_test", 10) == (ovr,)
        assert store.upsert_overlay(uow, overlay)
        assert not store.upsert_overlay(uow, replace(overlay, ended_at=NOW + timedelta(hours=2)))
        assert not store.upsert_overlay(uow, replace(overlay, revision=1, ended_at=None, state="active"))
        assert store.overlays_for(uow, "p_test", NOW, 10) == (overlay,)


@pytest.mark.trace("T-E25-025", "T-E25-102")
def test_config_generated_validation_and_bounded_cache() -> None:
    bounds = Bounds(500, 500, 64)
    first = handle()
    release = parse_documents(first, bounds)
    assert release.items and release.words and release.question_sets
    assert RoutineReleaseCheck(bounds).check(CandidateRelease(first.release_id, first.documents)) == ()
    cache = ReleaseCache(1, bounds)
    cached_first = cache.release(first)
    assert cached_first is cache.release(first)
    second = handle("rel_second")
    cache.release(second)
    assert cache.release(first) is not cached_first
    missing = MemoryRelease("rel_missing", {})
    with pytest.raises(ConfigInvalid):
        cache.release(missing)
    assert RoutineReleaseCheck(bounds).check(CandidateRelease(missing.release_id, missing.documents))


@pytest.mark.trace("T-E25-043", "T-E25-100", "T-E25-093")
def test_instances_targets_generations_clock_and_barriers_roundtrip(tmp_path: Path) -> None:
    from crew_health.routine.domain.instances import QuestionInstance, apply_answer
    from crew_health.routine.domain.override import end, repoint
    from crew_health.routine.domain.release import AddQuestions

    db = database(tmp_path)
    db.migrate(migration_sql())
    store = SqliteRoutineStore()
    ovr = replace(override(), effect=AddQuestions("qs_test", "next_occurrence", (120,), "team default"))
    inst = QuestionInstance(
        "rq_test",
        ovr.override_id,
        "p_test",
        "qs_test",
        "initial",
        ("q_test", "q_other"),
        NOW,
        NOW + timedelta(hours=1),
        {},
        "open",
        1,
    )
    with db.begin(actor=Actor("engine", "engine"), correlation_id="cor_1") as uow:
        assert store.insert_override(uow, ovr)
        assert store.insert(uow, occurrence())
        assert store.insert_instance(uow, inst, carrier_day=1, carrier_occurrence_id="ro_test")
        assert not store.insert_instance(uow, inst, carrier_day=1, carrier_occurrence_id="ro_test")
        assert store.get_instance(uow, inst.instance_id) == inst
        assert store.instances_for_occurrence(uow, "ro_test", 10) == (inst,)
        assert store.instance_carrier(uow, inst.instance_id) == (1, "ro_test")
        store.add_target(uow, ovr.override_id, "instance", inst.instance_id)
        store.add_target(uow, ovr.override_id, "instance", inst.instance_id)
        assert store.targets_of(uow, ovr.override_id, 10) == (("instance", inst.instance_id),)
        assert store.overrides_targeting(uow, inst.instance_id, 10) == (ovr,)
        updated = apply_answer(inst, question_id="q_test", answer_id="ans_1", answered_at=NOW)
        assert updated is not None
        store.update_instance(uow, updated, expected_revision=1)
        assert store.open_instances_for_person(uow, "p_test", 10) == (updated,)
        repointed = repoint(ovr, "wrn_2")
        store.update_override(uow, repointed, expected_revision=1)
        assert store.active_overrides_from_warning(uow, "wrn_1", 10) == ()
        assert store.active_overrides_from_warning(uow, "wrn_2", 10) == (repointed,)
        with pytest.raises(StaleVersion):
            store.update_override(uow, repointed, expected_revision=1)
        assert store.deadline_generation(uow, "routine.occ.ro_test") is None
        store.set_deadline_generation(uow, "routine.occ.ro_test", 1)
        store.set_deadline_generation(uow, "routine.occ.ro_test", 2)
        assert store.deadline_generation(uow, "routine.occ.ro_test") == 2
        store.put_barrier_ask(uow, "p_test", 1, ("vision",), "pending", None, NOW)
        assert store.get_barrier_ask(uow, "p_test", 1) == (("vision",), "pending", None, NOW)
        store.put_barrier_ask(uow, "p_test", 1, ("vision",), "answered", "ans_1", NOW)
        assert store.latest_barrier_day(uow, "p_test") == 1
        ended = end(repointed, at=NOW, reason="warning_cleared")
        store.update_override(uow, ended, expected_revision=2)
        assert store.active_overrides(uow, 10) == ()
    for statement in ("UPDATE routine_override SET revision=revision+1", "DELETE FROM routine_override"):
        with (
            pytest.raises(StorageUnavailable),
            db.begin(actor=Actor("engine", "engine"), correlation_id="cor_2") as uow,
        ):
            uow.connection.execute(statement)


@pytest.mark.trace("T-E25-025", "T-E25-102")
def test_broken_optional_document_is_rejected_and_missing_optional_is_allowed() -> None:
    import json
    from dataclasses import replace

    first = handle()
    required: dict[ConfigName, ConfigDocumentView] = {
        name: value
        for name, value in first.documents.items()
        if name in (ConfigName.ROUTINE_SCHEDULE, ConfigName.ROUTINE_RULES)
    }
    release = parse_documents(MemoryRelease("rel_optional_absent", required), Bounds(500, 500, 64))
    assert release.words is None and release.question_sets is None
    raw = json.dumps({"invalid": True}).encode()
    docs = dict(first.documents)
    docs[ConfigName.UI_COPY] = ConfigDocumentView(ConfigName.UI_COPY, sha256(raw).hexdigest(), raw)
    check = RoutineReleaseCheck(Bounds(500, 500, 64))
    assert check.check(CandidateRelease("rel_broken_ui", docs))
    with pytest.raises(ConfigInvalid):
        parse_documents(replace(first, release_id="rel_bad", documents=docs), Bounds(500, 500, 64))


@pytest.mark.trace("T-E25-002", "INV-E25-16")
def test_insert_does_not_silently_ignore_a_broken_invariant(tmp_path: Path) -> None:
    db = database(tmp_path)
    db.migrate(migration_sql())
    store = SqliteRoutineStore()
    invalid = replace(occurrence(), due_until=NOW - timedelta(seconds=1))
    with (
        pytest.raises(StorageUnavailable),
        db.begin(actor=Actor("engine", "engine"), correlation_id="cor_1") as uow,
    ):
        store.insert(uow, invalid)


@pytest.mark.trace("T-E25-025", "INV-E25-16")
def test_loader_rejects_bounds_and_configuration_edit_changes_content() -> None:
    import json

    first = handle()
    with pytest.raises(ConfigInvalid):
        parse_documents(first, Bounds(1, 500, 64))
    original = parse_documents(first, Bounds(500, 500, 64))
    schedule = json.loads(first.document(ConfigName.ROUTINE_SCHEDULE).raw)
    schedule["items"][0]["name"] = "Changed configured item label"
    raw = json.dumps(schedule).encode()
    docs = dict(first.documents)
    docs[ConfigName.ROUTINE_SCHEDULE] = ConfigDocumentView(
        ConfigName.ROUTINE_SCHEDULE, sha256(raw).hexdigest(), raw
    )
    changed = parse_documents(MemoryRelease("rel_changed", docs), Bounds(500, 500, 64))
    assert changed.items[0].name == "Changed configured item label"
    assert original.items[0].name != changed.items[0].name


@pytest.mark.trace("T-E25-010", "T-E25-043", "T-E25-100")
def test_store_aggregate_counts_and_paged_target_reads(tmp_path: Path) -> None:
    from crew_health.routine.domain.instances import QuestionInstance
    from crew_health.routine.domain.override import end, repoint

    db = database(tmp_path)
    db.migrate(migration_sql())
    store = SqliteRoutineStore()
    ovr = override()
    with db.begin(actor=Actor("engine", "engine"), correlation_id="cor_1") as uow:
        assert store.insert(uow, occurrence())
        assistant = replace(
            occurrence(),
            occurrence_id="ro_assistant",
            cause_key="assistant:insight_1",
            cause_kind="assistant",
            insight_id="insight_1",
            periodic=False,
        )
        assert store.insert(uow, assistant)
        assert store.count_open_nonassistant(uow, "p_test") == 1
        assert store.count_assistant_for_day(uow, "p_test", 1) == 1
        assert store.count_assistant_for_day(uow, "p_test", 2) == 0
        assert store.get_assistant_by_insight(uow, "insight_1") == assistant
        assert store.get_assistant_by_insight(uow, "insight_absent") is None
        assert store.insert_override(uow, ovr)
        assert store.count_active_overrides_for(uow, "p_test") == 1
        assert store.count_active_overrides_for(uow, "p_absent") == 0
        inst = QuestionInstance(
            "rq_test",
            ovr.override_id,
            "p_test",
            "qs_test",
            "initial",
            ("q_test",),
            NOW,
            NOW + timedelta(hours=1),
            {},
            "open",
            1,
        )
        assert store.insert_instance(uow, inst, carrier_day=1, carrier_occurrence_id=None)
        assert store.instances_for_day(uow, "p_test", 1, 10) == (inst,)
        assert store.instances_for_day(uow, "p_test", 2, 10) == ()
        assert store.open_instances_for_person(uow, "p_test", 1, offset=1) == ()
        assert len(store.open_for_person(uow, "p_test", 1, offset=1)) == 1
        store.add_target(uow, ovr.override_id, "occurrence", "ro_test")
        store.add_target(uow, ovr.override_id, "instance", "rq_test")
        assert store.targets_of(uow, ovr.override_id, 1) == (("instance", "rq_test"),)
        assert store.targets_of(uow, ovr.override_id, 1, offset=1) == (("occurrence", "ro_test"),)
        repointed = repoint(ovr, "wrn_2")
        store.update_override(uow, repointed, expected_revision=1)
        ended = end(repointed, at=NOW, reason="warning_cleared")
        store.update_override(uow, ended, expected_revision=2)
        assert store.overrides_from_warning(uow, "wrn_1", 1) == (ended,)
        assert store.overrides_from_warning(uow, "wrn_2", 1) == (ended,)
        assert store.count_active_overrides_for(uow, "p_test") == 0


@pytest.mark.trace("T-E25-043", "T-E25-100", "T-E25-002")
def test_every_multirow_reader_can_reach_the_second_bounded_page(tmp_path: Path) -> None:
    from crew_health.routine.domain.bindings import OverlayInput
    from crew_health.routine.domain.instances import QuestionInstance

    db = database(tmp_path)
    db.migrate(migration_sql())
    store = SqliteRoutineStore()
    with db.begin(actor=Actor("engine", "engine"), correlation_id="cor_pages") as uow:
        first_occ = occurrence()
        assert store.insert(uow, first_occ)
        for index in range(2):
            ovr = replace(override(), override_id=f"rov_page_{index}")
            assert store.insert_override(uow, ovr)
            store.add_target(uow, ovr.override_id, "occurrence", first_occ.occurrence_id)
            inst = QuestionInstance(
                f"rq_page_{index}",
                ovr.override_id,
                "p_test",
                "qs_test",
                "initial",
                ("q_test",),
                NOW,
                NOW + timedelta(hours=1),
                {},
                "open",
                1,
            )
            assert store.insert_instance(
                uow, inst, carrier_day=1, carrier_occurrence_id=first_occ.occurrence_id
            )
            assert store.upsert_overlay(
                uow,
                OverlayInput(
                    f"ovl_page_{index}",
                    1,
                    "p_test",
                    "sleep",
                    "ended",
                    None,
                    NOW,
                    NOW + timedelta(minutes=1),
                    None,
                ),
            )
            original = replace(first_occ, occurrence_id=f"ro_missed_{index}", cause_key=f"missed:{index}")
            missed, _ = miss(original, now=NOW, counted=True, reason="deadline", actor_id="engine")
            assert store.insert(uow, missed)
        assert store.active_overrides_for(uow, "p_test", 1, offset=1)[0].override_id == "rov_page_1"
        assert store.active_overrides_from_warning(uow, "wrn_1", 1, offset=1)[0].override_id == "rov_page_1"
        assert store.overrides_from_warning(uow, "wrn_1", 1, offset=1)[0].override_id == "rov_page_1"
        assert (
            store.overrides_targeting(uow, first_occ.occurrence_id, 1, offset=1)[0].override_id
            == "rov_page_1"
        )
        assert store.instances_for_day(uow, "p_test", 1, 1, offset=1)[0].instance_id == "rq_page_1"
        assert (
            store.instances_for_occurrence(uow, first_occ.occurrence_id, 1, offset=1)[0].instance_id
            == "rq_page_1"
        )
        assert store.overlays_for(uow, "p_test", NOW, 1, offset=1)[0].overlay_id == "ovl_page_1"
        assert store.misses_since(uow, "p_test", 1, 1, offset=1)[0].occurrence_id == "ro_missed_1"
