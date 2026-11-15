"""config/role-rules.json against its schema and the package; loader checks R-L1 to R-L4; config edits change output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease
from crew_health.kernel.testing import MemoryRelease
from crew_health.roles.application import config as module
from crew_health.roles.application.config import ConfigCache, RolesReleaseCheck, build_config
from crew_health.roles.domain.errors import RulesInvalid

from .harness import (
    REPO,
    ROLE_RULES,
    SCHEMAS,
    Harness,
    by_role,
    document,
    documents,
    example,
    help_session,
    obj,
    objs,
)

FLOWS = REPO / "docs/product/responder-flows.json"


def _validator(schema_file: str) -> Draft202012Validator:
    resources = []
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resources.append((path.name, Resource.from_contents(schema)))
    registry: Registry = Registry().with_resources(resources)
    return Draft202012Validator(
        json.loads((SCHEMAS / schema_file).read_text(encoding="utf-8")), registry=registry
    )


def _rules() -> dict[str, object]:
    value = json.loads(ROLE_RULES.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _flow_ids() -> list[str]:
    return [str(f["id"]) for f in json.loads(FLOWS.read_text(encoding="utf-8"))["flows"]]


@pytest.mark.trace("R54")
def test_role_rules_validates_against_its_schema() -> None:
    errors = sorted(_validator("config-role-rules.schema.json").iter_errors(_rules()), key=str)
    assert errors == []


@pytest.mark.trace("R54")
def test_role_rules_covers_every_responder_flow_in_order() -> None:
    rules = _rules()
    flows = rules["flow_roles"]
    assert isinstance(flows, list)
    assert [f["flow_id"] for f in flows] == _flow_ids()  # R-L1: no flow falls back to default_roles
    assert rules["notice"] and rules["basis"] == "team default"  # provenance: unreviewed draft, team default
    candidate = CandidateRelease(
        "rel_c",
        {**documents(), ConfigName.RESPONDER_FLOWS: document(ConfigName.RESPONDER_FLOWS, FLOWS.read_bytes())},
    )
    assert RolesReleaseCheck().check(candidate) == ()


@pytest.mark.trace("R54")
def test_package_example_flows_are_copied_exactly() -> None:
    package = {f["flow_id"]: f for f in objs(example("config/role-rules.example.json")["flow_roles"])}
    ours = {f["flow_id"]: f for f in objs(_rules()["flow_roles"])}
    for flow_id, flow in package.items():
        assert ours[flow_id] == flow
    for key in (
        "roles",
        "eligibility",
        "medical_officer_cascade",
        "default_roles",
        "plan_step_role",
        "reason_phrases",
    ):
        assert _rules()[key] == example("config/role-rules.example.json")[key]


def _mutated(edit: dict[str, object]) -> bytes:
    return json.dumps({**_rules(), **edit}).encode()


@pytest.mark.parametrize(
    ("edit", "pointer"),
    [
        ({"eligibility": [{"rule_id": "never_subject", "kind": "prefer", "text": "x"}]}, "/eligibility"),
        ({"medical_officer_cascade": []}, "/medical_officer_cascade"),
        ({"plan_step_role": "medical_officer"}, "/plan_step_role"),
        (
            {"reason_phrases": {**obj(_rules()["reason_phrases"]), "free_to_help": "Free {nobody}"}},
            "/reason_phrases",
        ),
    ],
)
@pytest.mark.trace("R54", "EI9")
def test_loader_rejects_a_document_that_weakens_safety(edit: dict[str, object], pointer: str) -> None:
    docs = {**documents(), ConfigName.ROLE_RULES: document(ConfigName.ROLE_RULES, _mutated(edit))}
    findings = RolesReleaseCheck().check(CandidateRelease("rel_bad", docs))
    assert findings and any(f.pointer.startswith(pointer) for f in findings)
    with pytest.raises(RulesInvalid):
        build_config("rel_bad", docs)


@pytest.mark.trace("R54")
def test_missing_or_corrupt_configuration_is_rejected() -> None:
    docs = documents()
    del docs[ConfigName.SKILLS_CATALOG]
    with pytest.raises(RulesInvalid):
        build_config("rel_missing", docs)
    corrupt = {**documents(), ConfigName.ROLE_RULES: document(ConfigName.ROLE_RULES, b"{not json")}
    assert RolesReleaseCheck().check(CandidateRelease("rel_corrupt", corrupt))
    unknown_skill = _rules()
    flows = unknown_skill["flow_roles"]
    assert isinstance(flows, list)
    flows[0]["roles"][0]["skill_id"] = "juggling"
    docs = {
        **documents(),
        ConfigName.ROLE_RULES: document(ConfigName.ROLE_RULES, json.dumps(unknown_skill).encode()),
    }
    with pytest.raises(RulesInvalid):
        build_config("rel_unknown_skill", docs)


@pytest.mark.trace("R54")
def test_missing_document_fails_closed_over_http(tmp_path: Path) -> None:
    h = Harness(tmp_path, role_rules=b"{}")
    h.open_help(help_session("hlp_x", subject="ilya", flow="not_breathing"))
    status, problem = h.get("/v1/help-sessions/hlp_x/roles", "station")
    assert status == 422 and problem["code"] == "CONFIG_INVALID"


@pytest.mark.trace("R54")
def test_configuration_edit_changes_content_without_code(tmp_path: Path) -> None:
    """AGENTS rule 1: a scratch copy of role-rules with a new label and phrase changes the served view."""
    edited = _rules()
    edited["reason_phrases"] = {**obj(edited["reason_phrases"]), "free_to_help": "Ready to help"}
    edited["roles"] = [
        {**role, "label": "Fetches the kit"} if role["role_id"] == "kit_fetcher" else role
        for role in objs(edited["roles"])
    ]
    scratch = tmp_path / "role-rules.json"
    scratch.write_text(json.dumps(edited), encoding="utf-8")
    h = Harness(tmp_path, role_rules=scratch.read_bytes())
    h.practice("tomas", "cpr")
    h.open_help(help_session("hlp_edit", subject="ilya", flow="not_breathing"))
    kit = by_role(h.roles("hlp_edit"))["kit_fetcher"]
    assert (kit["label"], kit["reason_text"]) == ("Fetches the kit", "Ready to help")


def test_config_cache_is_bounded() -> None:
    cache = ConfigCache()
    for i in range(module.CACHE_MAX + 3):
        cache.get(MemoryRelease(f"rel_{i}", documents()))
    assert len(cache._items) == module.CACHE_MAX
