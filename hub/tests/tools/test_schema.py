from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.tools.adapters.jsonschema_validator import ActionValidator
from crew_health.tools.domain.arguments import JsonBounds, parse_json
from crew_health.tools.domain.errors import ToolsInvalid


@pytest.mark.trace("T-E12-001", "T-E12-002", "R17", "I1", "I2")
@pytest.mark.parametrize(
    "nested",
    [
        {},
        {"type": "object", "properties": {}},
        {"$ref": "https://example.invalid/schema"},
        {"type": "object", "properties": {"patient_id": {"type": "string"}}, "additionalProperties": False},
    ],
)
def test_catalog_refuses_unfinished_open_remote_or_scope_schemas(nested: dict[str, object]) -> None:
    import json

    from crew_health.kernel.testing import MemorySettings
    from crew_health.tools.adapters.jsonschema_validator import CatalogValidator
    from crew_health.tools.settings import setting_specs
    from tests.tools.test_bounds import settings_values  # type: ignore[import-not-found]

    raw = json.dumps(
        {
            "tools": [
                {
                    "name": "get_capabilities",
                    "stage": "intake",
                    "effect": "read",
                    "input_schema": {
                        "type": "object",
                        "properties": {"nested": nested},
                        "additionalProperties": False,
                    },
                }
            ]
        }
    ).encode()
    with pytest.raises(ToolsInvalid):
        CatalogValidator(MemorySettings(setting_specs(), settings_values())).compile(
            raw, model_release_id="model_a"
        )


@pytest.mark.trace("T-E12-001", "T-E12-065", "I1", "I8")
def test_catalog_never_ignores_an_unbound_exposure_declaration() -> None:
    from crew_health.kernel.testing import MemorySettings
    from crew_health.tools.adapters.jsonschema_validator import CatalogValidator
    from crew_health.tools.settings import setting_specs
    from tests.tools.test_bounds import settings_values

    raw = b'{"tools":[],"exposure_sets":{"intake":[]}}'
    with pytest.raises(ToolsInvalid):
        CatalogValidator(MemorySettings(setting_specs(), settings_values())).compile(
            raw, model_release_id="model_a"
        )


BOUNDS = JsonBounds(max_bytes=8192, max_depth=16, max_array_items=50, max_string_chars=2000)


@pytest.mark.trace("T-E12-001", "T-E12-005", "I2")
@pytest.mark.parametrize(
    "raw",
    [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e9999}', b'{"x":"\\ud800"}', b"\xff"],
)
def test_malformed_or_nonfinite_json_is_rejected(raw: bytes) -> None:
    with pytest.raises(ToolsInvalid):
        parse_json(raw, BOUNDS)


@pytest.mark.trace("T-E12-005", "I2")
@pytest.mark.parametrize("raw", [b"[" * 17 + b"0" + b"]" * 17, b"x" * 8193, b'"' + b"x" * 2001 + b'"'])
def test_parser_rejects_bounded_resources(raw: bytes) -> None:
    with pytest.raises(ToolsInvalid):
        parse_json(raw, BOUNDS)


@pytest.mark.trace("T-E12-001", "T-E12-005", "I2")
@given(
    st.dictionaries(
        st.text(max_size=10),
        st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=10)),
        max_size=10,
    )
)
def test_json_parser_preserves_literals(value: dict[str, object]) -> None:
    assert parse_json(json.dumps(value).encode(), BOUNDS) == value


@pytest.mark.trace("T-E12-002", "R17")
@pytest.mark.parametrize(
    ("action", "extra"),
    [
        ("ask", {"question": "Question", "choices": []}),
        ("request_measurement", {"capability_code": "pulse", "body_region": None}),
        ("retrieve_evidence", {"query": "literal topic", "source_ids": []}),
        ("assessment", {"assessment_id": "assessment_1"}),
        ("request_review", {"to": "crew_medical_officer", "note": "Review"}),
        ("stop_incomplete", {"missing": ["missing input"]}),
    ],
)
def test_all_six_generated_actions_preserve_nullable_decision(action: str, extra: dict[str, object]) -> None:
    value = {
        "action": action,
        "reason": "reason",
        "decision_affected": None,
        "state_snapshot_version": 1,
        **extra,
    }
    result = ActionValidator(BOUNDS).parse(json.dumps(value).encode())
    assert result.root.action == action
    assert result.root.decision_affected is None
    value["patient_id"] = "foreign"
    with pytest.raises(ToolsInvalid):
        ActionValidator(BOUNDS).parse(json.dumps(value).encode())


@pytest.mark.trace("T-E12-002", "R17")
@pytest.mark.parametrize("region", ["heart", None, "unknown"])
def test_region_is_validated_by_generated_contract(region: str | None) -> None:
    value = {
        "action": "request_measurement",
        "reason": "reason",
        "decision_affected": None,
        "state_snapshot_version": 1,
        "capability_code": "pulse",
        "body_region": region,
    }
    if region == "unknown":
        with pytest.raises(ToolsInvalid):
            ActionValidator(BOUNDS).parse(json.dumps(value).encode())
    else:
        result = ActionValidator(BOUNDS).parse(json.dumps(value).encode())
        assert result.root.action == "request_measurement"


@pytest.mark.trace("T-E12-005", "I2")
def test_array_limit_and_quoted_brackets() -> None:
    assert parse_json(b'{"x":"[[[\\""}', BOUNDS) == {"x": '[[["'}
    with pytest.raises(ToolsInvalid):
        parse_json(json.dumps([0] * 51).encode(), BOUNDS)


@pytest.mark.trace("T-E12-005", "I2")
@pytest.mark.parametrize("value", [0, -1, True])
def test_parser_bounds_reject_invalid_configuration(value: int) -> None:
    with pytest.raises(ToolsInvalid):
        JsonBounds(max_bytes=value, max_depth=16, max_array_items=50, max_string_chars=2000)


@pytest.mark.trace("T-E12-001", "I2")
def test_finite_numbers_are_preserved_and_unmatched_closing_bracket_rejected() -> None:
    assert parse_json(b'{"value":1.5}', BOUNDS) == {"value": 1.5}
    with pytest.raises(ToolsInvalid):
        parse_json(b"]", BOUNDS)


@pytest.mark.trace("T-E12-005", "I2")
@pytest.mark.parametrize("decoded", [{1: "invalid key"}, float("inf"), [None] * 5])
def test_independent_decoded_value_guard_rejects_corruption_or_exhausted_work(decoded: object) -> None:
    from crew_health.tools.domain.arguments import _walk

    bounds = JsonBounds(max_bytes=1, max_depth=16, max_array_items=50, max_string_chars=2000)
    with pytest.raises(ToolsInvalid):
        _walk(decoded, bounds)
