"""Kernel release contributor checks real package configuration documents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, ConfigName
from crew_health.plans.application.release_check import PlansReleaseCheck
from crew_health.plans.application.settings import PlansSettings

from .harness import obj, rows


def example_candidate() -> CandidateRelease:
    root = Path(__file__).resolve().parents[3]
    paths = {
        ConfigName.ONBOARD_PLANS: "docs/product/api/examples/config/onboard-plans.example.json",
        ConfigName.KIT_MAP: "docs/product/api/examples/kit-map.example.json",
        ConfigName.SKILLS_CATALOG: "docs/product/api/examples/config/skills-catalog.example.json",
        ConfigName.ENVIRONMENT_PROFILES: "docs/product/api/examples/config/environment-profiles.example.json",
        ConfigName.RESPONDER_FLOWS: "docs/product/responder-flows.json",
    }
    documents = {}
    for name, path in paths.items():
        raw = obj(json.loads((root / path).read_text(encoding="utf-8")))
        value = raw.get("value", raw)
        encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
        documents[name] = ConfigDocumentView(name, hashlib.sha256(encoded).hexdigest(), encoded)
    return CandidateRelease("rel_test", documents)


def check_settings() -> PlansSettings:
    return PlansSettings(300, 40, 16, 500, 64, 2000, 512, 200, 4194304)


def edit_document(candidate: CandidateRelease, name: ConfigName, raw: dict[str, object]) -> CandidateRelease:
    encoded = json.dumps(raw, separators=(",", ":")).encode("utf-8")
    documents = dict(candidate.documents)
    documents[name] = ConfigDocumentView(name, hashlib.sha256(encoded).hexdigest(), encoded)
    return CandidateRelease(candidate.release_id, documents)


@pytest.mark.trace("T28-17", "T28-18", "R57")
def test_scratch_candidate_from_examples_without_unverifiable_annotations_passes() -> None:
    assert PlansReleaseCheck(check_settings()).check(candidate_without_unverifiable_annotations()) == ()


def candidate_without_unverifiable_annotations() -> CandidateRelease:
    candidate = example_candidate()
    raw = obj(json.loads(candidate.documents[ConfigName.ONBOARD_PLANS].raw))
    for plan in rows(raw["plans"]):
        for stage in rows(plan["stages"]):
            if "rtdc" in stage:
                stage["rtdc"] = None
            for step in rows(stage.get("steps", stage.get("prepare", []))):
                step["replaces_ground_task"] = None
    return edit_document(candidate, ConfigName.ONBOARD_PLANS, raw)


@pytest.mark.trace("T28-18", "EI4")
def test_unmodified_examples_reject_unverifiable_quotes_and_rtdc() -> None:
    findings = PlansReleaseCheck(check_settings()).check(example_candidate())
    assert len(findings) == 4
    assert sum("form quote unverifiable" in f.message for f in findings) == 3
    assert sum("rtdc unverifiable" in f.message for f in findings) == 1


@pytest.mark.trace("V82", "T28-18")
def test_v82_rejects_row_and_environment_without_aboard_plan() -> None:
    candidate = example_candidate()
    raw = obj(json.loads(candidate.documents[ConfigName.ONBOARD_PLANS].raw))
    plan = rows(raw["plans"])[0]
    plan["environments"] = ["lunar_surface"]
    candidate = edit_document(candidate, ConfigName.ONBOARD_PLANS, raw)
    findings = PlansReleaseCheck(check_settings()).check(candidate)
    assert any("ICL10" in f.message and "transit" in f.message for f in findings)
    assert all(f.check_id == "plans.onboard_plans" for f in findings)


@pytest.mark.trace("T28-18", "EI4")
def test_exception_inside_check_is_a_rejection() -> None:
    with patch("crew_health.plans.application.release_check.check_plans", side_effect=RuntimeError):
        findings = PlansReleaseCheck(check_settings()).check(example_candidate())
    assert len(findings) == 1
    assert findings[0].document == ConfigName.ONBOARD_PLANS


@pytest.mark.trace("T28-17", "R57")
def test_candidate_without_optional_onboard_plans_has_no_findings() -> None:
    assert PlansReleaseCheck().check(CandidateRelease("rel_empty", {})) == ()


@pytest.mark.trace("V82", "T28-18")
def test_simulation_row_gaps_are_counted_warnings_and_not_rejects() -> None:
    candidate = candidate_without_unverifiable_annotations()
    raw = obj(json.loads(candidate.documents[ConfigName.KIT_MAP].raw))
    rows(raw["conditions"])[0]["plan_id"] = None
    check = PlansReleaseCheck(check_settings(), mode_provider=lambda: "SIM")
    assert check.check(edit_document(candidate, ConfigName.KIT_MAP, raw)) == ()
    assert check.warning_count == 1
