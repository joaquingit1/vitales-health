"""E12's closed result contracts for the handlers it wires (E12 3 ToolResult, finding 3 of review r1).

The pinned E17 catalog closes the arguments; the result data of each wired tool is closed here. No field outside
these schemas reaches the controller, so a handler cannot smuggle a failed value, a warning level or an action.
"""

from __future__ import annotations

from jsonschema import Draft202012Validator

_ID: dict[str, object] = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}
_TEXT: dict[str, object] = {"type": "string", "maxLength": 20000}

_PASSAGE_PROPERTIES: dict[str, object] = {
    "passage_id": _ID,
    "release_id": _ID,
    "use": {"enum": ["risk_context", "patient_instruction", "routine_basis", "limit_basis"]},
    "content_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "text": _TEXT,
    "section": {"type": "string", "maxLength": 2000},
    "page": {"type": ["integer", "null"], "minimum": 1},
    "clinical_review": {"enum": ["clinically_reviewed", "not_clinically_reviewed"]},
    "use_status": {"type": ["string", "null"], "maxLength": 64},
    "source_ids": {"type": "array", "maxItems": 50, "items": _ID},
    "extraction_id": _ID,
    "discrepancy_refs": {"type": "array", "maxItems": 50, "items": _ID},
}


def _passage(*, support: bool) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [*sorted(_PASSAGE_PROPERTIES), "usable_as_support"],
        "properties": {**_PASSAGE_PROPERTIES, "usable_as_support": {"const": support}},
    }


_RETRIEVAL: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["passages", "history"],
    "properties": {
        "passages": {
            "type": "array",
            "maxItems": 8,
            "items": _passage(support=True),
        },
        "history": {
            "type": "array",
            "maxItems": 200,
            "items": _passage(support=False),
        },
    },
}

_PROPOSAL: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["draft", "capability"],
    "properties": {
        "draft": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "capability_code",
                "reason",
                "decision_affected",
                "body_region",
                "condition_ids",
                "source_ids",
            ],
            "properties": {
                "capability_code": {"type": "string", "minLength": 1, "maxLength": 128},
                "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
                "decision_affected": {"type": "string", "minLength": 1, "maxLength": 2000},
                "body_region": {"type": ["string", "null"], "maxLength": 32},
                "condition_ids": {"type": "array", "maxItems": 50, "items": _ID},
                "source_ids": {"type": "array", "maxItems": 50, "items": _ID},
            },
        },
        "capability": {
            "type": "object",
            "additionalProperties": False,
            "required": ["code", "status", "unavailable_reason"],
            "properties": {
                "code": {"type": "string", "minLength": 1, "maxLength": 128},
                "status": {"enum": ["available", "unavailable", "not_applicable"]},
                "unavailable_reason": {
                    "enum": [
                        None,
                        "not_in_kit",
                        "device_absent",
                        "operator_absent",
                        "context_inapplicable",
                        "adapter_down",
                    ]
                },
            },
        },
    },
}

_SCHEMAS: dict[str, dict[str, object]] = {
    "search_evidence": _RETRIEVAL,
    "read_source_passage": _RETRIEVAL,
    "propose_measurement": _PROPOSAL,
}


def result_validator(name: str) -> Draft202012Validator | None:
    schema = _SCHEMAS.get(name)
    return None if schema is None else Draft202012Validator(schema)
