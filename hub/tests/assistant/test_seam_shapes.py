import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.trace("SYSTEM.6.4", "T-E13-011", "T-E13-033")
def test_structural_provider_copies_match_frozen_owner_fields_and_signatures() -> None:
    contracts = json.loads(
        (Path(__file__).parent / "contracts/provider-shapes.json").read_text(encoding="utf-8")
    )
    for contract in contracts:
        source = ast.parse(
            (ROOT / "hub/src/crew_health/assistant/application" / contract["consumer_file"]).read_text(
                encoding="utf-8"
            )
        )
        classes = {node.name: node for node in source.body if isinstance(node, ast.ClassDef)}
        for name, expected in contract["classes"].items():
            node = classes[name]
            actual_fields = {
                n.target.id: ast.unparse(n.annotation)
                for n in node.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            }
            actual_methods = {
                n.name: {
                    "arguments": ast.unparse(n.args),
                    "returns": None if n.returns is None else ast.unparse(n.returns),
                }
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
            }
            assert actual_fields == expected["fields"], (contract["owner_path"], name)
            assert actual_methods == expected["methods"], (contract["owner_path"], name)


@pytest.mark.trace("SYSTEM.6.4", "V25")
def test_record_authorization_read_signature_matches_real_domain_bearing_owner() -> None:
    import inspect

    from crew_health.assistant.application.ports import RecordAuthorization as Consumer
    from crew_health.records.application.seams import RecordAuthorization as Owner

    assert inspect.signature(Consumer.require_read) == inspect.signature(Owner.require_read)
