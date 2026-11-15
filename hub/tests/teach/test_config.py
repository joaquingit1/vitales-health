"""The skills catalog as configuration (E30 section 8; AGENTS rule 1): config/skills-catalog.json validates against
its schema, a configuration edit changes content with no code change, and the loader check rejects a broken
candidate (T30-27, T30-37)."""

from __future__ import annotations

import copy
import hashlib
import json
from fractions import Fraction
from pathlib import Path
from typing import cast

import jsonschema
import pytest
import referencing

from crew_health.app.registry import DeclarationRegistry
from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView
from crew_health.kernel.testing import MemoryConfig, MemoryRelease, MemoryTemplateRenderer
from crew_health.teach.adapters.catalog_checks import SkillsCatalogCheck
from crew_health.teach.application.content import RENDER_FIELDS, ContentLoader, parse_catalog, render_steps
from crew_health.teach.application.settings import SPECS
from crew_health.teach.domain.agreement import evaluate_count_agreement
from crew_health.teach.domain.catalog import question_id
from crew_health.teach.domain.errors import CatalogShapeError
from crew_health.teach.module import register

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "contracts/api/schemas"
CATALOG = ROOT / "config/skills-catalog.json"
NAMES = {"helper_name": "Tomas", "subject_name": "Mara"}

type Doc = dict[str, object]


def document() -> Doc:
    return cast(Doc, json.loads(CATALOG.read_text(encoding="utf-8")))


def pulse(doc: Doc) -> Doc:
    skills = cast(list[Doc], doc["skills"])
    return next(s for s in skills if s["skill_id"] == "pulse_check_wrist")


def view(name: ConfigName, raw: bytes) -> ConfigDocumentView:
    return ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)


def example(path: str) -> bytes:
    return json.dumps(json.loads((ROOT / path).read_text(encoding="utf-8"))["value"]).encode()


def documents(catalog: bytes) -> dict[ConfigName, ConfigDocumentView]:
    return {
        ConfigName.SKILLS_CATALOG: view(ConfigName.SKILLS_CATALOG, catalog),
        ConfigName.UI_COPY: view(
            ConfigName.UI_COPY, example("contracts/api/examples/config/ui-copy.example.json")
        ),
        ConfigName.INDICATOR_CATALOG: view(
            ConfigName.INDICATOR_CATALOG,
            example("contracts/api/examples/config/indicator-catalog.example.json"),
        ),
        ConfigName.RESPONDER_FLOWS: view(
            ConfigName.RESPONDER_FLOWS, (ROOT / "docs/product/responder-flows.json").read_bytes()
        ),
    }


@pytest.mark.trace("T30-27")
def test_catalog_validates_against_its_schema() -> None:
    resources = [
        (p.name, referencing.Resource.from_contents(json.loads(p.read_text(encoding="utf-8"))))
        for p in SCHEMAS.glob("*.json")
    ]
    registry = referencing.Registry().with_resources(resources)
    schema = json.loads((SCHEMAS / "config-skills-catalog.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(
        schema, registry=registry, format_checker=jsonschema.FormatChecker()
    )
    validator.validate(document())


def test_taught_skill_comes_from_config() -> None:
    catalog = parse_catalog(CATALOG.read_bytes())
    skill = catalog.skill("pulse_check_wrist")
    assert skill is not None and skill.count_check is not None and skill.count_check.tolerance is not None
    assert skill.count_check.tolerance.value == 10 and skill.count_check.experimental
    assert catalog.notice == "Unreviewed draft: needs clinician review"
    count = skill.count_step(None)
    assert count is not None and count.count_seconds == 60
    assert question_id(skill.skill_id, 5) == "pulse_check_wrist_q5"


@pytest.mark.trace("AGENTS-1")
def test_config_edit_changes_tolerance_step_text_and_phrase() -> None:
    edited = document()
    skill = pulse(edited)
    check = cast(list[Doc], skill["checks"])[0]
    cast(Doc, check["tolerance"])["value"] = 3
    cast(Doc, check["phrases"])["ok"] = "Count {count}, cameras {camera}: close enough"
    steps = cast(list[Doc], skill["teach_steps"])
    steps[0]["text"] = "{subject_name}: rest your arm on the table, palm up."
    raw = json.dumps(edited).encode()
    release = MemoryRelease("rel_edit", documents(raw))
    content = ContentLoader(MemoryConfig([release], active_id="rel_edit"), max_cached=2).load(
        ContentLoader.pins_of(release)
    )
    definition = content.skill("pulse_check_wrist")
    assert definition.count_check is not None and definition.count_check.tolerance is not None
    tolerance = definition.count_check.tolerance.value
    assert evaluate_count_agreement(Fraction(92), Fraction(96), tolerance).result == "disagree"
    assert definition.count_check.phrases["ok"] == "Count {count}, cameras {camera}: close enough"
    rendered = render_steps(
        content, MemoryTemplateRenderer("unknown", RENDER_FIELDS), definition.steps, NAMES
    )
    assert rendered[0].text == "Mara: rest your arm on the table, palm up."


@pytest.mark.trace("T30-37")
def test_shape_errors_are_typed() -> None:
    broken = document()
    cast(list[Doc], pulse(broken)["teach_steps"])[1]["step_no"] = 9
    with pytest.raises(CatalogShapeError):
        parse_catalog(json.dumps(broken).encode())
    with pytest.raises(CatalogShapeError):
        parse_catalog(b"{not json")


@pytest.mark.trace("T30-27")
def test_loader_check_accepts_the_release_and_rejects_broken_candidates() -> None:
    check = SkillsCatalogCheck()
    assert check.check(CandidateRelease("rel_ok", documents(CATALOG.read_bytes()))) == ()
    broken = document()
    cast(list[Doc], pulse(broken)["teach_steps"])[1]["step_no"] = 9
    findings = check.check(CandidateRelease("rel_bad", documents(json.dumps(broken).encode())))
    assert len(findings) == 1 and findings[0].document is ConfigName.SKILLS_CATALOG
    dangling = document()
    for item in cast(list[Doc], dangling["skills"]):
        for step in cast(list[Doc], item["teach_steps"]):
            if step["from_flow_step"] is not None:
                step["from_flow_step"] = {**cast(Doc, step["from_flow_step"]), "index": 999}
    findings = check.check(CandidateRelease("rel_dangling", documents(json.dumps(dangling).encode())))
    assert findings and all("C6" in f.message for f in findings)
    missing_indicator = copy.deepcopy(document())
    reference = cast(Doc, cast(list[Doc], pulse(missing_indicator)["checks"])[0]["reference"])
    reference["capability_code"] = "no_such_indicator"
    findings = check.check(CandidateRelease("rel_ind", documents(json.dumps(missing_indicator).encode())))
    assert [f.message for f in findings] == ["C6 indicator not found"]


def test_module_declares_bounded_settings_and_its_loader_check() -> None:
    registry = DeclarationRegistry(max_ports=64, max_hooks=128)
    register(registry)
    settings = registry.declarations["settings"]
    assert set(settings) == {spec.key for spec in SPECS}
    assert "teach.skills_catalog" in registry.declarations["loader_checks"]
    registry.freeze(required_loader_checks=frozenset({"teach.skills_catalog"}))
