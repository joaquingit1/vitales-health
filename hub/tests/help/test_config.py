"""The help configuration documents: schema valid, traceable to the package, rejected when broken (AGENTS 1, 3)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from help_support import REPO, SCHEMAS, config_raw, dig, example_value, ui_copy_raw
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.help.domain.docs import ConfigPin, pin_documents
from crew_health.help.domain.errors import HelpConfigInvalid


def _validator(schema_name: str) -> Draft202012Validator:
    registry: Registry[Any] = Registry().with_resources(
        (p.name, Resource.from_contents(json.loads(p.read_bytes()))) for p in sorted(SCHEMAS.glob("*.json"))
    )
    return Draft202012Validator(json.loads((SCHEMAS / schema_name).read_bytes()), registry=registry)


@pytest.mark.trace("CONTRACTS-11", "AGENTS-1")
@pytest.mark.parametrize(
    ("document", "schema"),
    [
        ("responder-flows", "config-responder-flows.schema.json"),
        ("help-presentation", "config-help-presentation.schema.json"),
    ],
)
def test_authored_documents_validate_against_their_schemas(document: str, schema: str) -> None:
    errors = sorted(_validator(schema).iter_errors(json.loads(config_raw(document))), key=str)
    assert errors == []


@pytest.mark.trace("CONTRACTS-11")
def test_ui_copy_used_by_the_tests_is_schema_valid() -> None:
    assert (
        sorted(_validator("config-ui-copy.schema.json").iter_errors(json.loads(ui_copy_raw())), key=str) == []
    )


@pytest.mark.trace("CONTRACTS-11", "AGENTS-1", "provenance")
def test_documents_trace_to_the_package_specifications() -> None:
    assert config_raw("responder-flows") == (REPO / "docs/product/responder-flows.json").read_bytes()
    authored = json.loads(config_raw("help-presentation"))
    package = example_value("config/help-presentation.example.json")
    differences = sorted(k for k in package if authored.get(k) != package[k])
    assert differences == ["assistant_line", "notice"]
    assert dig(authored, "assistant_line", "waiting_text") == dig(package, "assistant_line", "waiting_text")
    flows = json.loads(config_raw("responder-flows"))
    source_ids = {s["id"] for s in flows["sources"]}
    shared = (REPO / "docs/product/RESPONDER-FLOWS.md").read_text(encoding="utf-8")
    for flow in flows["flows"]:
        assert set(flow["source_ids"]) & source_ids, flow["id"]
        for outside in set(flow["source_ids"]) - source_ids:
            # Shared package IDs listed in RESPONDER-FLOWS.md; the citation line shows them as unknown titles.
            assert f"| {outside} |" in shared, outside


def _pin(flows: bytes, presentation: bytes) -> None:
    pins = (ConfigPin("responder-flows", "a"), ConfigPin("help-presentation", "b"), ConfigPin("ui-copy", "c"))
    pin_documents("rel_x", flows_raw=flows, presentation_raw=presentation, ui_raw=ui_copy_raw(), pins=pins)


@pytest.mark.trace("AGENTS-config-reject", "T-E26-088")
@pytest.mark.parametrize(
    "corruption",
    [
        "not json",
        "missing start",
        "unknown start",
        "dangling goes_to",
        "unknown flow level",
        "unknown placeholder",
    ],
)
def test_corrupt_documents_are_rejected_with_a_typed_error(corruption: str) -> None:
    flows = json.loads(config_raw("responder-flows"))
    presentation = json.loads(config_raw("help-presentation"))
    flows_raw: bytes | None = None
    if corruption == "not json":
        flows_raw = b"{"
    elif corruption == "missing start":
        del presentation["start_node_id"]
    elif corruption == "unknown start":
        presentation["start_node_id"] = "nowhere"
    elif corruption == "dangling goes_to":
        flows["triage"][0]["answers"][0]["goes_to"] = "nowhere"
    elif corruption == "unknown flow level":
        presentation["flow_levels"][0]["flow_id"] = "nowhere"
    else:
        presentation["log_templates"]["crew_called"] = "Crew called {secret}"
    with pytest.raises(HelpConfigInvalid):
        _pin(
            flows_raw if flows_raw is not None else json.dumps(flows).encode(),
            json.dumps(presentation).encode(),
        )


@pytest.mark.trace("AGENTS-1", "T-E26-081")
def test_valid_documents_pin() -> None:
    _pin(config_raw("responder-flows"), config_raw("help-presentation"))
