"""Fault injection for unknown eligibility, storage rollback, and corrupted pinned content."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import pytest

from crew_health.guidance.application.ports import (
    Fact,
    GuidanceDecision,
    GuidanceRequest,
    OperatorFact,
    Unknown,
)
from crew_health.kernel.config import ConfigDocumentView, ConfigName
from crew_health.kernel.errors import ErrorCode, ProblemError, StorageUnavailable
from crew_health.kernel.testing import MemoryRelease, MemorySettings
from crew_health.kernel.uow import ReadSnapshot
from crew_health.plans.application.eligibility import StepGate
from crew_health.plans.application.environment import EnvironmentReader
from crew_health.plans.application.pinning import load_context
from crew_health.plans.application.ports import StockLevel, SupplyNeed
from crew_health.plans.application.release import (
    PlansCatalog,
    UiCopy,
    document_view,
    kit_for,
    kit_supplies,
    month_days,
    packed_stock,
    pins,
    return_option_present,
)
from crew_health.plans.application.settings import PlansSettings
from crew_health.plans.domain.availability import assess_step, runnable_now
from crew_health.roles.application.ports import RosterMember

from .plans_stack import PLANS_VALUES, PlansStack, document, obj, rows, stack
from .test_plans_foundation import example_release, settings


@dataclass
class BrokenFacts:
    error: Exception

    def stock(self, environment_id: str) -> Mapping[str, StockLevel]:
        raise self.error

    def operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> Fact[tuple[OperatorFact, ...]]:
        raise self.error

    def crew(self, at: datetime) -> Sequence[RosterMember]:
        raise self.error

    def uses_after_one(
        self, environment_id: str, needs: Sequence[SupplyNeed], stock: Mapping[str, StockLevel]
    ) -> Mapping[tuple[str, str], int | None]:
        raise self.error


class UnknownPeople:
    def operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> Fact[tuple[OperatorFact, ...]]:
        return Unknown("roles", "roster_unverifiable")


def environment_reader(s: PlansStack, part: str, failure: BrokenFacts | None) -> EnvironmentReader:
    return EnvironmentReader(
        failure if part == "skills" else s.crew,
        failure if part == "roster" else s.crew,
        failure if part == "stock" else s.supplies,
        failure if part == "cover" else s.supplies,
    )


@pytest.mark.trace("SYSTEM-8.2", "H28-2")
@pytest.mark.parametrize("part", ["stock", "skills", "roster", "cover"])
@pytest.mark.parametrize("missing", [True, False])
def test_optional_fact_loss_never_invents_values_names_or_cover(
    tmp_path: Path, part: str, missing: bool, caplog: pytest.LogCaptureFixture
) -> None:
    s = stack(tmp_path)
    failure = None if missing else BrokenFacts(RuntimeError("optional service stopped"))
    reader = environment_reader(s, part, failure)
    with s.db.snapshot() as view:
        context = reader.context(view, environment_id="lunar_surface", subject_person_id="mara", needs=())
        if part == "stock":
            assert context.stock is None
        elif part == "skills":
            assert context.people is None
            doc = s.world.catalog.document(view.release)
            assert doc is not None
            plan = doc.plan("plan_icl10")
            assert plan is not None
            step = plan.step("icl10_pulse_by_crewmate")
            assert step is not None
            assessment = assess_step(step, plan, context)
            assert "people_unknown" in assessment.unknown
            assert not runnable_now(assessment)
        elif part == "roster":
            assert context.people is not None
            assert all(person.display_name is None for person in context.people)
        else:
            assert context.cover_after == {}
    if not missing:
        assert "plans_" + ("people" if part == "skills" else part) + "_unavailable" in caplog.text
    assert s.query("SELECT count(*) FROM plans_run") == ((0,),)


@pytest.mark.trace("SYSTEM-8.2", "H28-2")
def test_unknown_roster_is_not_an_empty_known_roster(tmp_path: Path) -> None:
    s = stack(tmp_path)
    with s.db.snapshot() as view:
        context = EnvironmentReader(UnknownPeople(), s.crew, s.supplies, s.supplies).context(
            view, environment_id="lunar_surface", subject_person_id="mara", needs=()
        )
    assert context.people is None


@pytest.mark.trace("EI12", "AGENTS-6")
@pytest.mark.parametrize("part", ["stock", "skills", "roster", "cover"])
@pytest.mark.parametrize("kind", ["storage", "typed"])
def test_fact_provider_storage_and_typed_failures_roll_back_the_callers_transaction(
    tmp_path: Path, part: str, kind: str
) -> None:
    s = stack(tmp_path)
    failure = (
        StorageUnavailable("disk_full")
        if kind == "storage"
        else ProblemError(ErrorCode.INVALID_VALUE, "provider invariant failed")
    )
    reader = environment_reader(s, part, BrokenFacts(failure))
    with pytest.raises(ProblemError) as error:
        with s.db.begin(actor=s.session.actor, correlation_id="cor_fact_failure") as uow:
            uow.connection.execute("INSERT INTO plans_test_debit VALUES (?,?)", ("marker", "supply"))
            reader.context(uow, environment_id="lunar_surface", subject_person_id="mara", needs=())
    assert error.value is failure
    assert s.query("SELECT count(*) FROM plans_test_debit") == ((0,),)


@dataclass
class GuidanceBoundary:
    error: Exception | None = None
    answer: GuidanceDecision | None = None
    request: GuidanceRequest | None = None

    def evaluate(self, snapshot: ReadSnapshot, request: GuidanceRequest) -> GuidanceDecision:
        self.request = request
        if self.error is not None:
            raise self.error
        if self.answer is None:
            raise RuntimeError("no verified guidance result")
        return self.answer


@pytest.mark.trace("SYSTEM-8.2", "H28-2")
@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_failed_guidance_cannot_turn_aboard_step_into_executable(
    tmp_path: Path, missing: bool, caplog: pytest.LogCaptureFixture
) -> None:
    s = stack(tmp_path)
    with s.db.snapshot() as view:
        doc = s.world.catalog.document(view.release)
        assert doc is not None
        plan = doc.plan("plan_icl10")
        assert plan is not None
        step = plan.step("icl10_pulse_by_crewmate")
        assert step is not None
        context = s.world.environment(view, "lunar_surface", "mara", ())
        assert runnable_now(assess_step(step, plan, context))
        gate = StepGate(None if missing else GuidanceBoundary(error=RuntimeError("guidance stopped")))
        assert not gate.runnable(
            view,
            plan=plan,
            step=step,
            origin_mode="SIM",
            subject_person_id="mara",
            context=context,
            release_id=view.release.release_id,
        )
    if not missing:
        assert "plans_guidance_unavailable" in caplog.text


@pytest.mark.trace("SYSTEM-8.2", "EI12")
@pytest.mark.parametrize("storage", [True, False])
def test_guidance_typed_error_propagates_and_rolls_back(tmp_path: Path, storage: bool) -> None:
    s = stack(tmp_path)
    failure = StorageUnavailable("corrupt") if storage else ProblemError(ErrorCode.CONFIG_INVALID, "bad pin")
    with pytest.raises(ProblemError) as error:
        with s.db.begin(actor=s.session.actor, correlation_id="cor_guidance_failure") as uow:
            doc = s.world.catalog.document(uow.release)
            assert doc is not None
            plan = doc.plan("plan_icl10")
            assert plan is not None
            uow.connection.execute("INSERT INTO plans_test_debit VALUES (?,?)", ("marker", "supply"))
            StepGate(GuidanceBoundary(error=failure)).executable(
                uow,
                plan=plan,
                step=plan.steps[0],
                origin_mode="SIM",
                subject_person_id="mara",
                release_id=uow.release.release_id,
            )
    assert error.value is failure
    assert s.query("SELECT count(*) FROM plans_test_debit") == ((0,),)


@pytest.mark.trace("SYSTEM-8.2", "R28-25")
def test_executable_bit_is_authoritative_and_full_guidance_decision_remains_available(tmp_path: Path) -> None:
    s = stack(tmp_path)
    answer = GuidanceDecision(
        "show",
        "aboard",
        False,
        ("tomas",),
        ("content_not_verified",),
        ("content",),
        "simulation_only",
        "not_clinically_reviewed",
        None,
    )
    boundary = GuidanceBoundary(answer=answer)
    gate = StepGate(boundary)
    with s.db.snapshot() as view:
        doc = s.world.catalog.document(view.release)
        assert doc is not None
        plan = doc.plan("plan_icl10")
        assert plan is not None
        step = plan.step("icl10_pulse_by_crewmate")
        assert step is not None
        pinned = view.release.release_id
        decision = gate.decision(
            view, plan=plan, step=step, origin_mode="SIM", subject_person_id="mara", release_id=pinned
        )
        assert decision is answer
        assert decision.availability == "aboard" and decision.who_can == ("tomas",)
        assert not gate.executable(
            view, plan=plan, step=step, origin_mode="SIM", subject_person_id="mara", release_id=pinned
        )
        assert boundary.request is not None
        assert boundary.request.content.content_sha256 == step.content_sha256
        assert boundary.request.release_id == pinned
        assert str(boundary.request.content.kind) == "plan_step"
        assert boundary.request.requirements.operator_skill_id == "pulse_check_wrist"
        prepare = replace(step, stage_id="return")
        gate.decision(
            view, plan=plan, step=prepare, origin_mode="SIM", subject_person_id="mara", release_id=pinned
        )
        assert boundary.request.content.kind == "prepare_step"


@dataclass
class FailingRelease:
    error: Exception
    release_id: str = "release_failure"

    def version_of(self, name: ConfigName) -> str:
        raise self.error

    def document(self, name: ConfigName) -> ConfigDocumentView:
        raise self.error


@pytest.mark.trace("PR-I9", "AGENTS-6")
@pytest.mark.parametrize(
    "failure", [StorageUnavailable("io_error"), ProblemError(ErrorCode.INTERNAL_ERROR, "reader failed")]
)
def test_configuration_storage_failure_is_not_treated_as_absent(failure: ProblemError) -> None:
    with pytest.raises(ProblemError) as error:
        document_view(FailingRelease(failure), ConfigName.ONBOARD_PLANS)
    assert error.value is failure


@pytest.mark.trace("PR-I9", "R28-25")
def test_absent_documents_leave_every_dependent_fact_unknown() -> None:
    release = MemoryRelease("release_empty", {})
    catalog = PlansCatalog(settings())
    assert catalog.document(release) is None
    assert catalog.raw_plan(release, "plan_icl10") is None
    assert document_view(FailingRelease(KeyError("missing")), ConfigName.KIT_MAP) is None
    assert kit_for(release, "lunar_surface") is None
    assert kit_supplies(release) == {}
    assert packed_stock(release) == {}
    assert not return_option_present(release, "lunar_surface")
    assert month_days(release) is None
    assert pins(release) == ()
    assert UiCopy.from_release(release) is None
    assert UiCopy({"plan_lines": {"recheck_pending": None}}).text("plan_lines", "recheck_pending") is None
    assert UiCopy({}).text("plan_lines", "recheck_pending") is None


def with_document(release: MemoryRelease, name: ConfigName, value: Mapping[str, object]) -> MemoryRelease:
    docs = dict(release.documents)
    docs[name] = document(name, value)
    return MemoryRelease("release_scratch", docs)


@pytest.mark.trace("PR-I9", "R28-25")
@pytest.mark.parametrize("raw", [b"{", b"[]"])
def test_corrupt_document_is_rejected_without_replacing_the_cached_valid_plan(raw: bytes) -> None:
    release = example_release()
    catalog = PlansCatalog(settings())
    valid = catalog.document(release)
    docs = dict(release.documents)
    docs[ConfigName.ONBOARD_PLANS] = ConfigDocumentView(
        ConfigName.ONBOARD_PLANS, hashlib.sha256(raw).hexdigest(), raw
    )
    with pytest.raises(ValueError):
        catalog.document(MemoryRelease("release_corrupt", docs))
    assert catalog.document(release) is valid


@pytest.mark.trace("PR-I9", "T28-39")
@pytest.mark.parametrize("bound", ["max_document_bytes", "max_steps_per_plan", "max_plans_per_document"])
def test_document_resource_limit_rejects_candidate_before_caching(bound: str) -> None:
    release = example_release()
    catalog = PlansCatalog(replace(settings(), **{bound: 1}))
    with pytest.raises(ValueError, match="bound"):
        catalog.document(release)


@pytest.mark.trace("PR-I9", "T28-09")
@pytest.mark.parametrize("failure", ["identity", "size", "steps"])
def test_pinned_plan_cache_refuses_wrong_identity_and_resource_exhaustion(failure: str) -> None:
    release = example_release()
    catalog = PlansCatalog(settings())
    raw = catalog.raw_plan(release, "plan_icl10")
    assert raw is not None
    limits = replace(
        settings(),
        **(
            {"max_document_bytes": 1}
            if failure == "size"
            else {"max_steps_per_plan": 1}
            if failure == "steps"
            else {}
        ),
    )
    with pytest.raises(ValueError, match="identity|bound"):
        PlansCatalog(limits).parse_pinned(
            "version",
            "other_plan" if failure == "identity" else "plan_icl10",
            json.dumps(raw),
            kit_supplies(release),
        )


class UnexpectedSettings(MemorySettings):
    def __init__(self, value: int) -> None:
        super().__init__(PlansSettings.specs(), PLANS_VALUES)
        self.value = value

    def integer(self, key: str) -> int:
        return self.value if key == "plans.max_text_chars" else super().integer(key)


@pytest.mark.trace("SYSTEM-5.11", "H28-2")
@pytest.mark.parametrize("value", [0, 20001, True])
def test_hub_settings_outside_declared_bounds_are_rejected_even_after_provider_validation(value: int) -> None:
    with pytest.raises(ProblemError) as error:
        PlansSettings.read(UnexpectedSettings(value))
    assert error.value.code is ErrorCode.CONFIG_INVALID


@pytest.mark.trace("PR-I9", "T28-09")
@pytest.mark.parametrize(
    "bad", ["hash", "json", "plan", "supplies", "supply_unit", "month_days", "copy", "oversize"]
)
def test_invalid_immutable_backup_never_supplies_guidance_metadata(tmp_path: Path, bad: str) -> None:
    s = stack(tmp_path)
    run = s.start()
    run_id = str(run["plan_run_id"])
    with s.db.begin(actor=s.session.actor, correlation_id="cor_backup_fault") as uow:
        stored = s.world.repository.pinned_context(uow, run_id)
        assert stored is not None
        envelope = obj(json.loads(stored[0]))
        limit = s.world.settings.max_document_bytes
        if bad == "json":
            raw = "{"
        else:
            if bad == "plan":
                envelope["plan"] = []
            elif bad == "supplies":
                envelope["supplies"] = {}
            elif bad == "supply_unit":
                envelope["supplies"] = [{"supply_id": "scratch", "unit": 5, "consumable": True}]
            elif bad == "month_days":
                envelope["month_days"] = True
            elif bad == "copy":
                envelope["ui_copy"] = []
            elif bad == "oversize":
                limit = 1
            raw = json.dumps(envelope)
        digest = "bad" if bad == "hash" else hashlib.sha256(raw.encode("utf-8")).hexdigest()
        uow.connection.execute(
            "UPDATE plans_run_context SET context_json=?,sha256=? WHERE plan_run_id=?", (raw, digest, run_id)
        )
        assert load_context(s.world.repository, uow, run_id, limit) is None
    assert s.query("SELECT revision,state FROM plans_run") == ((1, "active"),)


@pytest.mark.trace("PR-I9", "T28-09")
def test_corrupt_supply_metadata_never_reinterprets_pinned_amount_using_active_unit(tmp_path: Path) -> None:
    s = stack(tmp_path)
    run = s.start(plan_id="plan_eye_injury")
    run_id = str(run["plan_run_id"])
    with s.db.begin(actor=s.session.actor, correlation_id="cor_backup_fault") as uow:
        uow.connection.execute("UPDATE plans_run_context SET sha256='bad' WHERE plan_run_id=?", (run_id,))
    kit = obj(json.loads(s.docs[ConfigName.KIT_MAP].raw))
    next(row for row in rows(kit["supplies"]) if row["supply_id"] == "eye_shield")["unit"] = "new_unit"
    s.replace_document(ConfigName.KIT_MAP, kit)
    with s.db.snapshot() as view:
        persisted = s.world.load(view, run_id)
        _, restricted = s.world.plan(view, persisted)
        assert restricted, "the original supply units cannot be verified after pinned context corruption"
