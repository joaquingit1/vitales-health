from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.config import CandidateRelease, ConfigDocumentView
from crew_health.tools.adapters.jsonschema_validator import PolicyValidator
from crew_health.tools.domain.arguments import JsonBounds
from crew_health.tools.domain.errors import ToolsInvalid

BOUNDS = JsonBounds(262144, 16, 50, 2000)


@pytest.fixture
def policy() -> bytes:
    example = json.loads(Path("docs/product/api/examples/config/assistant-policy.example.json").read_bytes())
    return json.dumps(example["value"]).encode("utf-8")


@pytest.mark.trace("T-E12-063", "I8")
@pytest.mark.parametrize("mutation", ["missing", "unknown", "duplicate"])
def test_mandatory_safety_rules_cannot_be_removed_or_redefined(policy: bytes, mutation: str) -> None:
    value = json.loads(policy)
    if mutation == "missing":
        value["forbidden_output"].pop()
    elif mutation == "unknown":
        value["forbidden_output"][0]["id"] = "unbound"
    else:
        value["forbidden_output"].append(value["forbidden_output"][0])
    with pytest.raises(ToolsInvalid):
        PolicyValidator(BOUNDS).parse(json.dumps(value).encode())


@pytest.mark.trace("T-E12-064", "I8")
def test_validated_content_edit_changes_read_notice(policy: bytes) -> None:
    validator = PolicyValidator(BOUNDS)
    before = validator.parse(policy)
    value = json.loads(policy)
    value["notice"] = before.notice + " (configuration edit)"
    after = validator.parse(json.dumps(value).encode())
    assert after.notice == before.notice + " (configuration edit)"


@pytest.mark.trace("T-E12-063", "I8")
def test_loader_contributor_returns_safe_finding(policy: bytes) -> None:
    raw = b"{}"
    document = ConfigDocumentView(ConfigName.ASSISTANT_POLICY, hashlib.sha256(raw).hexdigest(), raw)
    candidate = CandidateRelease("candidate", {ConfigName.ASSISTANT_POLICY: document})
    findings = PolicyValidator(BOUNDS).check(candidate)
    assert len(findings) == 1
    assert findings[0].document == ConfigName.ASSISTANT_POLICY
    good = ConfigDocumentView(ConfigName.ASSISTANT_POLICY, hashlib.sha256(policy).hexdigest(), policy)
    assert PolicyValidator(BOUNDS).check(CandidateRelease("good", {ConfigName.ASSISTANT_POLICY: good})) == ()


@pytest.mark.trace("T-E12-063", "I8")
def test_registered_policy_check_is_mandatory_and_binds_startup_settings(
    tools_declarations, policy: bytes
) -> None:
    check = tools_declarations.declarations["loader_checks"]["tools.assistant-policy"]
    assert isinstance(check, PolicyValidator)
    document = ConfigDocumentView(ConfigName.ASSISTANT_POLICY, hashlib.sha256(policy).hexdigest(), policy)
    candidate = CandidateRelease("candidate", {ConfigName.ASSISTANT_POLICY: document})
    assert check.check(candidate)
    tools_declarations.resolve()
    assert check.check(candidate) == ()
    invalid = ConfigDocumentView(ConfigName.ASSISTANT_POLICY, hashlib.sha256(b"{}").hexdigest(), b"{}")
    assert check.check(CandidateRelease("invalid", {ConfigName.ASSISTANT_POLICY: invalid}))
