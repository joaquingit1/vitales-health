import hashlib
import json
from pathlib import Path

import pytest

from crew_health.assistant.application.content import Copy, CopyDocument, Policy
from crew_health.kernel.errors import ProblemError

ROOT = Path(__file__).resolve().parents[3]


def policy(raw: bytes) -> Policy:
    return Policy.parse(raw, max_bytes=65536, max_iterations=32, max_timeout_seconds=300)


@pytest.mark.trace("T-E13-080", "T-E13-081", "T-E13-082")
def test_actual_policy_is_closed_sourced_and_preserves_team_operational_budgets() -> None:
    value = policy((ROOT / "config/assistant-policy.json").read_bytes())
    assert value.max_iterations == 8 and value.max_schema_repairs == 1
    assert value.turn_timeout_seconds == 90 and value.first_token_timeout_seconds == 20
    assert value.allowed_warning_levels == frozenset({"info", "watch"})
    assert "not clinically reviewed" in value.notice.lower()


@pytest.mark.trace("T-E13-080", "INV-E13-11")
@pytest.mark.parametrize(
    "field,value",
    [
        ("max_iterations", 0),
        ("max_iterations", 33),
        ("max_iterations", True),
        ("max_schema_repairs", 2),
        ("turn_timeout_seconds", 301),
        ("first_token_timeout_seconds", 91),
        ("tool_timeout_seconds", 91),
        ("allowed_warning_levels", ["act"]),
    ],
)
def test_policy_cannot_expand_caps_or_safety_levels(field: str, value: object) -> None:
    doc = json.loads((ROOT / "config/assistant-policy.json").read_bytes())
    doc[field] = value
    with pytest.raises(ProblemError):
        policy(json.dumps(doc).encode())


@pytest.mark.trace("T-E13-080", "T-E13-082")
def test_copy_pin_and_release_are_checked_and_nonsafety_edit_is_visible_without_code_change() -> None:
    raw = (ROOT / "config/assistant-ui.json").read_bytes()
    version = hashlib.sha256(raw).hexdigest()
    current = Copy.parse(CopyDocument(raw, version, "rel_A"), release_id="rel_A", max_bytes=65536)
    doc = json.loads(raw)
    doc["states"]["stopped"] = "Changed copy under a new pin"
    changed = json.dumps(doc).encode()
    edited = Copy.parse(
        CopyDocument(changed, hashlib.sha256(changed).hexdigest(), "rel_B"),
        release_id="rel_B",
        max_bytes=65536,
    )
    assert edited.states["stopped"] != current.states["stopped"]
    with pytest.raises(ProblemError):
        Copy.parse(CopyDocument(changed, version, "rel_A"), release_id="rel_A", max_bytes=65536)
    with pytest.raises(ProblemError):
        Copy.parse(CopyDocument(raw, version, "rel_B"), release_id="rel_A", max_bytes=65536)


@pytest.mark.trace("T-E13-080", "T-E13-082")
def test_missing_notice_fails_closed_and_does_not_use_an_unknown_fallback() -> None:
    doc = json.loads((ROOT / "config/assistant-ui.json").read_bytes())
    del doc["notice"]["stale"]
    raw = json.dumps(doc).encode()
    with pytest.raises(ProblemError):
        Copy.parse(
            CopyDocument(raw, hashlib.sha256(raw).hexdigest(), "rel_A"), release_id="rel_A", max_bytes=65536
        )


@pytest.mark.trace("T-E13-080", "T-E13-090")
def test_interface_copy_validates_owned_closed_schema_and_policy_provenance_covers_each_budget() -> None:
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (ROOT / "hub/src/crew_health/assistant/schemas/assistant-ui.schema.json").read_bytes()
    )
    copy = json.loads((ROOT / "config/assistant-ui.json").read_bytes())
    Draft202012Validator(schema).validate(copy)
    provenance = json.loads((ROOT / "config/assistant-policy.provenance.json").read_bytes())
    assert (
        hashlib.sha256((ROOT / "config/assistant-policy.json").read_bytes()).hexdigest()
        == provenance["artifact_sha256"]
    )
    assert set(provenance["operational_basis"]["values"]) == {
        "max_iterations",
        "max_schema_repairs",
        "turn_timeout_seconds",
        "first_token_timeout_seconds",
        "tool_timeout_seconds",
    }
    assert all(source["section"] for topic in provenance["topics"] for source in topic["sources"])
