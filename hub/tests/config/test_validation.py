from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.config.adapters.verification import SchemaCheck, verified_documents
from crew_health.config.application.loader import ConfigLoader, LoaderChecks
from crew_health.config.domain.checks import IndicatorCheck, SourceCheck
from crew_health.config.domain.errors import ConfigInvalid
from crew_health.config.domain.templates import PinnedTemplateRenderer, TemplateCheck
from crew_health.config.domain.text_rules import AmountRules
from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView, Finding

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-08", "R06", "V75")]
ROOT = Path(__file__).resolve().parents[3]


def candidate(documents: dict[ConfigName, object]) -> CandidateRelease:
    views = {}
    for name, value in documents.items():
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
        views[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
    return CandidateRelease("release_test", views)


def test_missing_contributors_rejected_for_every_origin() -> None:
    checks = LoaderChecks(maximum=64)
    loader = ConfigLoader(checks, max_document_bytes=8388608)
    for origin in ("LIVE", "REPLAY", "SIM"):
        result = loader.validate(candidate({}), origin=origin)
        assert not result.passed
        assert {finding.check_id for finding in result.errors} >= {"1", "2", "9", "13"}


def test_all_schemas_execute_and_mutated_copy_changes_content() -> None:
    check = SchemaCheck.from_directory(ROOT / "contracts/api/schemas")
    path = ROOT / "docs/product/api/examples/config/ui-copy.example.json"
    value = json.loads(path.read_bytes())["value"]
    original = candidate({ConfigName.UI_COPY: value})
    assert check.check_document(original.documents[ConfigName.UI_COPY]) == ()
    value["designed_states"]["unknown"] = "Configuration changed label"
    changed = candidate({ConfigName.UI_COPY: value})
    assert check.check_document(changed.documents[ConfigName.UI_COPY]) == ()
    assert original.documents[ConfigName.UI_COPY].version != changed.documents[ConfigName.UI_COPY].version
    value["unexpected"] = True
    assert check.check_document(candidate({ConfigName.UI_COPY: value}).documents[ConfigName.UI_COPY])


def test_hash_inventory_rejects_missing_unlisted_and_tampered_input() -> None:
    raw = b'{"a":1}'
    name = ConfigName.UI_COPY
    inventory = {name: hashlib.sha256(raw).hexdigest()}
    assert verified_documents({name: raw}, inventory)[name].raw == raw
    for documents, hashes in (({}, inventory), ({name: b"{}"}, inventory), ({name: raw}, {})):
        with pytest.raises(ConfigInvalid):
            verified_documents(documents, hashes)


def test_sources_and_indicator_codes_fail_closed() -> None:
    source = SourceCheck(frozenset({"EV01"}))
    assert source.check(candidate({ConfigName.UI_COPY: {"source_ids": ["EV01"]}})) == ()
    assert source.check(candidate({ConfigName.UI_COPY: {"source_ids": ["EV99"]}}))
    indicators = IndicatorCheck(frozenset({"pulse"}))
    assert (
        indicators.check(candidate({ConfigName.INDICATOR_CATALOG: {"indicators": [{"code": "pulse"}]}})) == ()
    )
    assert indicators.check(candidate({ConfigName.INDICATOR_CATALOG: {"indicators": [{"code": "invented"}]}}))


def test_template_fields_and_escaped_syntax() -> None:
    check = TemplateCheck()
    assert (
        check.check(candidate({ConfigName.UI_COPY: {"designed_states": {"sensor_quiet": "At {time}"}}})) == ()
    )
    assert check.check(
        candidate({ConfigName.UI_COPY: {"designed_states": {"sensor_quiet": "{private_text}"}}})
    )
    assert check.check(candidate({ConfigName.UI_COPY: {"level_words": {"info": "{time}"}}}))
    assert check.check(candidate({ConfigName.UI_COPY: {"designed_states": {"sensor_quiet": "{time!r}"}}}))
    missing = []
    renderer = PinnedTemplateRenderer(unknown="Unknown", on_missing=missing.append)
    assert renderer.render("ui-copy/designed_states/sensor_quiet", "At {time}", {}) == "At Unknown"
    assert missing == ["ui-copy/designed_states/sensor_quiet"]


def test_amount_rule_is_bounded_and_uses_offsets_only() -> None:
    rules = AmountRules(max_text_bytes=100, max_matches=2)
    assert rules.find("Take 12 mg and 0.5 mL") == ((5, 10), (15, 21))
    assert rules.find("The length is 3 mm") == ()
    with pytest.raises(ConfigInvalid):
        rules.find("1 mg 2 mg 3 mg")
    with pytest.raises(ConfigInvalid):
        rules.find("x" * 101)


@given(st.integers(min_value=0, max_value=100000))
def test_amount_grammar_property(amount: int) -> None:
    text = f"{amount} mg"
    assert AmountRules(max_text_bytes=100, max_matches=1).find(text) == ((0, len(text)),)


def test_nonfinite_duplicate_and_excessively_nested_json_rejected() -> None:
    from crew_health.config.domain.checks import parse_document

    for raw in (b'{"a":NaN}', b'{"a":1e999}', b'{"a":1,"a":2}', b"[" * 2000 + b"]" * 2000):
        with pytest.raises(ConfigInvalid):
            parse_document(raw)


def test_overlay_unit_mapping_schema_and_independent_ucum_spelling() -> None:
    from jsonschema import Draft202012Validator

    schema = json.loads((ROOT / "contracts/decision-overlay/config-unit-mapping.schema.json").read_bytes())
    document = json.loads((ROOT / "config/unit-mapping.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)
    units = {(row["indicator_code"], row["wire_unit"]): row["ucum_unit"] for row in document["mappings"]}
    assert units[("pulse", "bpm")] == "/min"
    assert units[("co2", "mmHg")] == "mm[Hg]"
    assert units[("co2", "ppm")] == "[ppm]"
    assert all(row["rounding"] == {"decimal_places": 4, "mode": "half_even"} for row in document["mappings"])
    assert all(row["source_refs"] for row in document["mappings"])


def test_offline_artifact_manifest_rejects_missing_and_tampered_inputs() -> None:
    from crew_health.config.adapters.verification import ConfigArtifacts

    filenames = (
        "sources.csv",
        "NASA-SOURCES.md",
        "DEMO-CONDITIONS.md",
        "RESEARCH-ANNEX.md",
        "environment-hazards.json",
        "routine-schedule.json",
        "responder-flows.json",
    )
    artifacts = {name: (ROOT / "docs/product" / name).read_bytes() for name in filenames}
    artifacts["input-dictionary.json"] = (ROOT / "contracts/reference/input-dictionary.json").read_bytes()
    inventory = {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()}
    checked = ConfigArtifacts.from_verified(
        schema_directory=ROOT / "contracts/api/schemas", artifacts=artifacts, inventory=inventory
    )
    assert "EV01" in checked.sources.source_ids
    assert "pulse" in checked.indicators.field_ids
    for altered in (
        {name: raw for name, raw in artifacts.items() if name != "NASA-SOURCES.md"},
        dict(artifacts, **{"NASA-SOURCES.md": b"tampered"}),
    ):
        with pytest.raises(ConfigInvalid):
            ConfigArtifacts.from_verified(
                schema_directory=ROOT / "contracts/api/schemas", artifacts=altered, inventory=inventory
            )


def test_loader_registration_bounds_freeze_and_warning_rules() -> None:
    from crew_health.kernel.testing import MemoryActivationGuard, MemoryReleaseCheck

    check = MemoryReleaseCheck(
        "6",
        lambda candidate: (
            Finding("6", ConfigName.ROUTINE_RULES, "/bindings", "binding text mismatch"),
            Finding("6.unbound_trigger", ConfigName.ROUTINE_RULES, "/bindings", "unbound manual trigger"),
        ),
    )
    with pytest.raises(ConfigInvalid):
        LoaderChecks(maximum=0)
    checks = LoaderChecks(maximum=1)
    checks.register(check)
    for action in (
        lambda: checks.register(check),
        lambda: checks.register(MemoryReleaseCheck("another", lambda candidate: ())),
        lambda: checks.register_guard(MemoryActivationGuard("guard", lambda uow, candidate: ())),
    ):
        with pytest.raises(ConfigInvalid):
            action()
    checks.freeze()
    with pytest.raises(ConfigInvalid):
        checks.register(check)
    with pytest.raises(ConfigInvalid):
        checks.register_guard(MemoryActivationGuard("guard", lambda uow, candidate: ()))
    with pytest.raises(ConfigInvalid):
        ConfigLoader(checks, max_document_bytes=0)
    result = ConfigLoader(checks, max_document_bytes=8388608).validate(candidate({}), origin="SIM")
    assert any(finding.message == "binding text mismatch" for finding in result.errors)
    assert tuple(finding.check_id for finding in result.warnings) == ("6.unbound_trigger",)
    guards = LoaderChecks(maximum=2)
    guard = MemoryActivationGuard("stock", lambda uow, candidate: ())
    guards.register_guard(guard)
    with pytest.raises(ConfigInvalid):
        guards.register_guard(guard)


def test_plan_gap_warning_never_waives_missing_check() -> None:
    from crew_health.kernel.config import Finding
    from crew_health.kernel.testing import MemoryReleaseCheck

    checks = LoaderChecks(maximum=20)
    checks.register(
        MemoryReleaseCheck(
            "15", lambda candidate: (Finding("15", ConfigName.KIT_MAP, "/conditions/0", "gap"),)
        )
    )
    loader = ConfigLoader(checks, max_document_bytes=10)
    for origin in ("SIM", "REPLAY"):
        result = loader.validate(candidate({}), origin=origin)
        assert len(result.warnings) == 1
        assert result.errors
    result = loader.validate(candidate({}), origin="LIVE")
    assert not result.warnings
    assert any(finding.check_id == "15" for finding in result.errors)
    damaged = CandidateRelease(
        "release_bad",
        {ConfigName.UI_COPY: ConfigDocumentView(ConfigName.ROLE_RULES, "0" * 64, b"{not_json_is_oversized")},
    )
    assert any(
        finding.message == "invalid JSON document"
        for finding in loader.validate(damaged, origin="SIM").errors
    )


def test_text_validation_is_release_bound_and_missing_medicines_stays_unavailable() -> None:
    from crew_health.config.adapters.repository import PinnedRelease
    from crew_health.config.application.loader import TextRulesService
    from crew_health.config.settings import specifications
    from crew_health.kernel.testing import MemorySettings

    settings = MemorySettings(
        specifications(),
        {"text_rules.max_text_bytes": 30, "text_rules.max_matches": 10, "config.ui_copy_cache_max": 1},
    )
    rules = TextRulesService(settings=settings)
    old = candidate({ConfigName.ASSISTANT_POLICY: {}})
    pinned = PinnedRelease("release_old", old.documents)
    assert rules.find_amounts("Use 2 mg", release=pinned).spans == ((4, 8),)
    unavailable = rules.find_medicines("hello", release=pinned)
    assert unavailable.release_id == "release_old"
    assert unavailable.state == "unavailable"
    assert unavailable.spans == ()
    with pytest.raises(ConfigInvalid):
        rules.find_medicines("x" * 31, release=pinned)
    with pytest.raises(ConfigInvalid):
        rules.find_amounts("hello", release=PinnedRelease("release_missing", {}))
    for size, matches in ((0, 1), (1, 0)):
        with pytest.raises(ConfigInvalid):
            AmountRules(size, matches)


def test_template_and_source_edge_cases_and_walk_budgets() -> None:
    from crew_health.config.domain.checks import walk

    assert TemplateCheck().check(candidate({ConfigName.SKILLS_CATALOG: {"notice": "{helper_name}"}}))
    assert TemplateCheck().check(candidate({ConfigName.QUESTIONNAIRES: {"notice": "{value}"}}))
    assert TemplateCheck().check(candidate({ConfigName.UI_COPY: {"designed_states": {"unknown": "{time"}}}))
    renderer = PinnedTemplateRenderer("Unknown", lambda key: None)
    assert (
        renderer.render("ui-copy/designed_states/sensor_quiet", "At {time}", {"time": "12:30"}) == "At 12:30"
    )
    with pytest.raises(ConfigInvalid):
        renderer.render("ui-copy/level_words/info", "{time}", {"time": "12:30"})
    assert SourceCheck(frozenset()).check(candidate({ConfigName.UI_COPY: {"source_id": "EV99"}}))
    assert IndicatorCheck(frozenset()).check(candidate({}))
    with pytest.raises(ConfigInvalid):
        tuple(walk([1, 2], maximum=1))
    with pytest.raises(ConfigInvalid):
        tuple(walk([1], maximum=1))


def test_warning_and_help_placeholders_never_expand_in_other_fields() -> None:
    check = TemplateCheck()
    assert check.check(candidate({ConfigName.WARNING_RULES: {"notice": "{value}"}}))
    assert check.check(candidate({ConfigName.HELP_PRESENTATION: {"notice": "{subject_name}"}}))
