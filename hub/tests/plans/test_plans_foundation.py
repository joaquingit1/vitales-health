"""Durable aggregate round trips and configuration-only content changes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.kernel.config import ConfigDocumentView, ConfigName
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.testing import MemoryRelease, MemorySettings
from crew_health.kernel.uow import Actor
from crew_health.plans.adapters.repository import EscalationIntent, SqlitePlanRuns
from crew_health.plans.application.release import (
    PlansCatalog,
    UiCopy,
    kit_for,
    kit_supplies,
    month_days,
    packed_stock,
    pins,
    read_document,
    return_option_present,
)
from crew_health.plans.application.settings import PlansSettings
from crew_health.plans.domain.run import (
    ActionContext,
    Escalate,
    Escalation,
    LinkAtOpen,
    NotAvailable,
    PlanRun,
    StepDone,
    apply,
    open_run,
)
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database

from .harness import now, obj, raw_document, rows


def settings() -> PlansSettings:
    values = (300, 40, 16, 500, 64, 2000, 512, 200, 4194304)
    keys = tuple(spec.key for spec in PlansSettings.specs())
    return PlansSettings.read(MemorySettings(PlansSettings.specs(), dict(zip(keys, values, strict=True))))


def example_release() -> MemoryRelease:
    base = Path(__file__).resolve().parents[3] / "docs/product/api/examples"
    names = (
        ConfigName.ONBOARD_PLANS,
        ConfigName.KIT_MAP,
        ConfigName.SKILLS_CATALOG,
        ConfigName.ENVIRONMENT_PROFILES,
        ConfigName.ROUTINE_RULES,
        ConfigName.UI_COPY,
    )
    docs = {}
    for name in names:
        path = base / (name.value + ".example.json")
        if not path.exists():
            path = base / "config" / (name.value + ".example.json")
        example = obj(json.loads(path.read_text(encoding="utf-8")))
        raw = json.dumps(example.get("value", example)).encode("utf-8")
        docs[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
    return MemoryRelease("release_1", docs)


@pytest.mark.trace("PR-I9", "T28-09", "T28-39")
def test_repository_round_trip_records_children_intents_and_pinned_hash(tmp_path: Path) -> None:
    release = example_release()
    catalog = PlansCatalog(settings())
    doc = catalog.document(release)
    assert doc is not None
    plan = doc.plan("plan_icl10")
    assert plan is not None
    run = open_run(
        plan=plan,
        environment_id="lunar_surface",
        plan_run_id="prun_1",
        pins=pins(release),
        opened_release_id=release.release_id,
        subject_person_id="subject",
        episode_id="ep_1",
        help_session_id=None,
        origin_mode="SIM",
        opened_by=Actor("person", "subject"),
        at=now(),
        link=LinkAtOpen("down", "unknown", None),
        return_option_present=True,
    )
    db = database(tmp_path)
    migration = (
        Path(__file__).resolve().parents[2] / "src/crew_health/plans/adapters/migrations/0001_plans.sql"
    )
    sql = migration.read_text(encoding="utf-8")
    db.migrate_tracked("plans.0001", hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql)
    repo = SqlitePlanRuns(settings())
    ctx = ActionContext(
        plan,
        {s.step_id: True for s in run.steps},
        {s.step_id: "aboard" for s in run.steps},
        run.opened_by,
        now(),
        False,
        500,
        64,
        30,
    )
    with db.begin(actor=run.opened_by, correlation_id="cor_1") as uow:
        repo.pin_plan(
            uow, doc.document_sha256, plan.plan_id, json.dumps(catalog.raw_plan(release, plan.plan_id))
        )
        repo.insert(uow, run, doc.document_sha256)
        assert repo.load(uow, run.plan_run_id) == run
        step = run.steps[0]
        updated, _ = apply(run, NotAvailable(step.step_id, "unavailable", run.revision), ctx)
        repo.save(uow, updated)
        assert repo.load(uow, run.plan_run_id) == updated
        intent = EscalationIntent(
            "pes_1",
            run.plan_run_id,
            "step_1",
            1,
            updated.revision,
            "subject",
            None,
            release.release_id,
            "SIM",
            "{}",
            "{}",
            "command_1",
            "subject",
            "pending",
            None,
        )
        repo.insert_intent(uow, intent)
        assert repo.intent(uow, "pes_1") == intent
        assert repo.pending_intents(uow, run.plan_run_id) == (intent,)
        repo.record_intent(uow, "pes_1", "help_1")
        assert repo.intent(uow, "pes_1") == replace(intent, state="recorded", help_session_id="help_1")
    with db.snapshot() as view:
        assert repo.version(view, run.plan_run_id) == doc.document_sha256
        assert repo.list_open(view, "subject", None, 50) == (updated,)
        assert repo.open_for(view, plan.plan_id, "subject", "ep_1") == run.plan_run_id
        assert repo.open_for(view, plan.plan_id, "other", "ep_1") is None
        pinned = repo.pinned_plan_json(view, doc.document_sha256, plan.plan_id)
        assert pinned is not None and hashlib.sha256(pinned[0].encode("utf-8")).hexdigest() == pinned[1]


@pytest.mark.trace("T28-09", "T28-39", "R28-25")
def test_configuration_changes_content_and_unknown_document_stays_absent() -> None:
    release = example_release()
    catalog = PlansCatalog(settings())
    first = catalog.document(release)
    assert first is not None
    assert catalog.document(release) is first
    raw = raw_document()
    item = rows(raw["plans"])[0]
    item["title"] = "Scratch configuration title"
    data = json.dumps(raw).encode("utf-8")
    docs = dict(release.documents)
    docs[ConfigName.ONBOARD_PLANS] = ConfigDocumentView(
        ConfigName.ONBOARD_PLANS, hashlib.sha256(data).hexdigest(), data
    )
    second = catalog.document(MemoryRelease("release_2", docs))
    assert second is not None and second.plans[0].title == "Scratch configuration title"
    assert first.plans[0].title != second.plans[0].title
    assert read_document(MemoryRelease("release_3", {}), ConfigName.ONBOARD_PLANS) is None
    assert kit_for(release, "lunar_surface") is not None
    assert kit_for(release, "unknown") is None
    assert kit_supplies(release)["eye_rinse_water"].unit.code == "{rinse}"
    assert packed_stock(release)["lunar_surface"]["eye_rinse_water"].aboard
    assert return_option_present(release, "lunar_surface")
    assert not return_option_present(release, "unknown")
    assert month_days(release) == 30
    ui = UiCopy.from_release(release)
    assert ui is not None and ui.text("plan_lines", "recheck_pending") is not None
    assert UiCopy.render("{what}: {n}", {"what": "count", "n": "2"}) == "count: 2"


@pytest.mark.trace("T28-13", "PR-I9")
def test_repository_preserves_step_observation_order_and_rolls_back(tmp_path: Path) -> None:
    release = example_release()
    doc = PlansCatalog(settings()).document(release)
    assert doc is not None
    plan = doc.plan("plan_icl10")
    assert plan is not None
    run = open_run(
        plan=plan,
        environment_id="lunar_surface",
        plan_run_id="prun_1",
        pins=pins(release),
        opened_release_id=release.release_id,
        subject_person_id="subject",
        episode_id="ep_1",
        help_session_id=None,
        origin_mode="SIM",
        opened_by=Actor("person", "subject"),
        at=now(),
        link=LinkAtOpen("down", None, None),
        return_option_present=True,
    )
    db = database(tmp_path)
    migration = (
        Path(__file__).resolve().parents[2] / "src/crew_health/plans/adapters/migrations/0001_plans.sql"
    )
    sql = migration.read_text(encoding="utf-8")
    db.migrate_tracked("plans.0001", hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql)
    repo = SqlitePlanRuns(settings())
    with db.begin(actor=run.opened_by, correlation_id="cor_1") as uow:
        repo.insert(uow, run, doc.document_sha256)
    ctx = ActionContext(
        plan,
        {s.step_id: True for s in run.steps},
        {s.step_id: "aboard" for s in run.steps},
        run.opened_by,
        now(),
        False,
        500,
        64,
        30,
    )
    updated, _ = apply(run, StepDone(run.steps[0].step_id, ("obs_z", "obs_a"), None, 1), ctx)
    escalation_step = next(s for s in plan.steps if s.stage_id == "if_worse" and s.opens_help)
    updated, _ = apply(updated, Escalate(escalation_step.step_id, "help_1", "pes_1", updated.revision), ctx)
    with pytest.raises(StorageUnavailable):
        with db.begin(actor=run.opened_by, correlation_id="cor_2") as uow:
            repo.save(uow, updated)
            assert repo.load(uow, run.plan_run_id) == updated
            assert repo.runs_with_open_escalation(uow, "help_1") == (updated,)
            raise StorageUnavailable("disk_full")
    with db.snapshot() as view:
        assert repo.load(view, run.plan_run_id) == run


@pytest.mark.trace("PR-I9", "T28-09")
def test_same_plan_hash_reloads_supply_units_from_changed_kit_map() -> None:
    release = example_release()
    catalog = PlansCatalog(settings())
    before = catalog.document(release)
    assert before is not None
    old_plan = before.plan("plan_eye_injury")
    assert old_plan is not None
    old_step = old_plan.step("eye_object_shield")
    assert old_step is not None
    old_amount = old_step.needs.supplies[0].per_use
    assert old_amount is not None
    kit = read_document(release, ConfigName.KIT_MAP)
    assert kit is not None
    changed = obj(json.loads(json.dumps(kit)))
    supply = next(row for row in rows(changed["supplies"]) if row["supply_id"] == "eye_shield")
    supply["unit"] = "changed_piece"
    data = json.dumps(changed).encode("utf-8")
    docs = dict(release.documents)
    docs[ConfigName.KIT_MAP] = ConfigDocumentView(ConfigName.KIT_MAP, hashlib.sha256(data).hexdigest(), data)
    after = catalog.document(MemoryRelease("release_2", docs))
    assert after is not None
    new_plan = after.plan("plan_eye_injury")
    assert new_plan is not None
    new_step = new_plan.step("eye_object_shield")
    assert new_step is not None
    new_amount = new_step.needs.supplies[0].per_use
    assert new_amount is not None
    assert new_amount.unit.code == "{changed_piece}"
    assert old_amount.unit != new_amount.unit


@pytest.mark.trace("SYSTEM-8.1", "PR-I9")
def test_context_is_immutable_hashed_and_occurrence_does_not_reuse_joined_help(tmp_path: Path) -> None:
    release = example_release()
    doc = PlansCatalog(settings()).document(release)
    assert doc is not None
    plan = doc.plan("plan_icl10")
    assert plan is not None
    run = open_run(
        plan=plan,
        environment_id="lunar_surface",
        plan_run_id="prun_1",
        pins=pins(release),
        opened_release_id=release.release_id,
        subject_person_id="subject",
        episode_id="ep_1",
        help_session_id=None,
        origin_mode="SIM",
        opened_by=Actor("person", "subject"),
        at=now(),
        link=LinkAtOpen("down", None, None),
        return_option_present=True,
    )
    db = database(tmp_path)
    migration = (
        Path(__file__).resolve().parents[2] / "src/crew_health/plans/adapters/migrations/0001_plans.sql"
    )
    sql = migration.read_text(encoding="utf-8")
    db.migrate_tracked("plans.0001", hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql)
    repo = SqlitePlanRuns(settings())
    with db.begin(actor=run.opened_by, correlation_id="cor_1") as uow:
        assert repo.pinned_context(uow, run.plan_run_id) is None
        repo.pin_context(uow, run.plan_run_id, '{"version":"original"}')
        repo.insert(uow, run, doc.document_sha256)
        repo.pin_context(uow, run.plan_run_id, '{"version":"replacement"}')
        context = repo.pinned_context(uow, run.plan_run_id)
        assert context is not None
        assert context[0] == '{"version":"original"}'
        assert hashlib.sha256(context[0].encode("utf-8")).hexdigest() == context[1]
        assert repo.intent_count(uow, run.plan_run_id) == 0
        assert repo.next_occurrence(uow, run.plan_run_id, "step_1") == 1
        intent = EscalationIntent(
            "pes_1",
            run.plan_run_id,
            "step_1",
            1,
            1,
            "subject",
            None,
            release.release_id,
            "SIM",
            "{}",
            "{}",
            "command_1",
            "subject",
            "pending",
            None,
        )
        repo.insert_intent(uow, intent)
        repo.record_intent(uow, intent.escalation_id, "shared_help")
        assert repo.intent_count(uow, run.plan_run_id) == 1
        assert repo.next_occurrence(uow, run.plan_run_id, "step_1") == 2
        repo.insert_intent(uow, replace(intent, escalation_id="pes_2", occurrence=2))
        repo.record_intent(uow, "pes_2", "shared_help")
        assert repo.next_occurrence(uow, run.plan_run_id, "step_1") == 3
        assert repo.intent_count(uow, run.plan_run_id) == 2


@pytest.mark.trace("PR-I9", "T28-09", "T28-39")
def test_pinned_parse_cache_is_bounded_and_uses_resolved_supply_metadata() -> None:
    release = example_release()
    bounded = replace(settings(), parsed_plan_cache_entries=1)
    catalog = PlansCatalog(bounded)
    raw = catalog.raw_plan(release, "plan_eye_injury")
    assert raw is not None
    supplies = kit_supplies(release)
    first = catalog.parse_pinned("original", "plan_eye_injury", json.dumps(raw), supplies)
    assert catalog.parse_pinned("original", "plan_eye_injury", json.dumps(raw), supplies) is first
    changed = dict(supplies)
    from crew_health.kernel.units import Unit

    changed["eye_shield"] = replace(changed["eye_shield"], unit=Unit("{changed_piece}"))
    second = catalog.parse_pinned("original", "plan_eye_injury", json.dumps(raw), changed)
    assert second is not first
    step = second.step("eye_object_shield")
    assert step is not None
    amount = step.needs.supplies[0].per_use
    assert amount is not None and amount.unit.code == "{changed_piece}"
    # The first entry was evicted at the configured single-entry bound.
    assert catalog.parse_pinned("original", "plan_eye_injury", json.dumps(raw), supplies) is not first


def stored_stack(tmp_path: Path, limits: PlansSettings) -> tuple[SQLiteDatabase, SqlitePlanRuns, PlanRun]:
    release = example_release()
    doc = PlansCatalog(settings()).document(release)
    assert doc is not None
    plan = doc.plan("plan_icl10")
    assert plan is not None
    run = open_run(
        plan=plan,
        environment_id="lunar_surface",
        plan_run_id="prun_1",
        pins=pins(release),
        opened_release_id=release.release_id,
        subject_person_id="subject",
        episode_id="ep_1",
        help_session_id=None,
        origin_mode="SIM",
        opened_by=Actor("person", "subject"),
        at=now(),
        link=LinkAtOpen("down", None, None),
        return_option_present=True,
    )
    db = database(tmp_path)
    migration = (
        Path(__file__).resolve().parents[2] / "src/crew_health/plans/adapters/migrations/0001_plans.sql"
    )
    sql = migration.read_text(encoding="utf-8")
    db.migrate_tracked("plans.0001", hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql)
    repo = SqlitePlanRuns(limits)
    with db.begin(actor=run.opened_by, correlation_id="cor_1") as uow:
        repo.insert(uow, run, doc.document_sha256)
    return db, repo, run


@pytest.mark.trace("T28-04", "R28-20")
def test_supply_references_are_paged_independently_of_step_count(tmp_path: Path) -> None:
    limits = settings()
    db, repo, run = stored_stack(tmp_path, limits)
    references = tuple("supply_use_" + str(i) for i in range(limits.max_steps_per_plan + 1))
    completed = replace(run.steps[0], runs=1, state="done", supply_use_ids=references)
    updated = replace(run, steps=(completed,) + run.steps[1:], revision=2)
    with db.begin(actor=run.opened_by, correlation_id="cor_2") as uow:
        repo.save(uow, updated)
        assert repo.load(uow, run.plan_run_id) == updated


@pytest.mark.trace("T28-01", "SYSTEM-8.1")
def test_shared_session_reconciliation_pages_every_run_and_rejects_total_over_bound(tmp_path: Path) -> None:
    from crew_health.kernel.errors import ErrorCode, ProblemError

    limits = replace(settings(), page_limit_max=2, max_plans_per_document=2)
    db, repo, run = stored_stack(tmp_path, limits)
    versions: list[PlanRun] = []
    with db.begin(actor=run.opened_by, correlation_id="cor_2") as uow:
        for n in range(3):
            version = replace(
                run,
                plan_run_id="prun_shared_" + str(n),
                episode_id="ep_shared_" + str(n),
                state="escalated",
                escalations=(Escalation("step_1", "shared_help", now(), False, None),),
            )
            repo.insert(uow, version, "plan_hash")
            versions.append(version)
        assert repo.runs_with_open_escalation(uow, "shared_help") == tuple(versions)
        # The finite aggregate ceiling is max_plans_per_document * page_limit_max.
        for n in range(3, 5):
            repo.insert(
                uow,
                replace(versions[0], plan_run_id="prun_shared_" + str(n), episode_id="ep_shared_" + str(n)),
                "plan_hash",
            )
        with pytest.raises(ProblemError) as error:
            repo.runs_with_open_escalation(uow, "shared_help")
        assert error.value.code is ErrorCode.PAYLOAD_TOO_LARGE
