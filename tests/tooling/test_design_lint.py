"""DESIGN-QUALITY-RCA 5.2: deterministic checks with positive and negative evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import design_lint as dl


def write(root: Path, path: str, text: str) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def write_json(root: Path, path: str, value: object) -> Path:
    return write(root, path, json.dumps(value, indent=2))


def design_text(changes: dict[str, str] | None = None) -> str:
    sections = {
        "1": ("Scope", "E99 owns CONTRACTS.md section 1. E99-01 R01 V01 D01."),
        "1.1": ("Rule trace", "CONTRACTS.md:3 | 3 | T99-01"),
        "2": ("Seams", "None"),
        "3": ("Domain model", "None"),
        "3.1": ("Time", "None"),
        "4": ("Application layer", "None"),
        "5": ("Adapters", "None"),
        "6": ("API", "None"),
        "7": ("User interface", "None"),
        "8": ("Configuration", "None"),
        "8.1": ("Activation effects", "None"),
        "9": ("Deployment and fallbacks", "None"),
        "10": ("Safety, security and privacy", "None"),
        "11": ("Performance and resources", "None"),
        "12": ("Test plan", "| Test ID | Markers |\n|---|---|\n| T99-01 | unit V01 D01 |"),
        "12.1": (
            "Acceptance matrix",
            "| Requirement | Test ID |\n|---|---|\n| E99-01 R01 V01 D01 | T99-01 |",
        ),
        "13": ("File ownership", "None"),
        "14": ("Requests and open questions", "None"),
        "15": ("Build plan", 'pytest -m "unit and not slow"'),
    }
    for key, value in (changes or {}).items():
        sections[key] = (sections[key][0], value)
    return (
        "# E99: Fixture\n\n"
        + "\n\n".join(
            f"{'###' if '.' in key else '##'} {key}. {title}\n\n{content}"
            for key, (title, content) in sections.items()
        )
        + "\n"
    )


@pytest.fixture
def package(tmp_path: Path) -> dl.Package:
    write(tmp_path, "docs/tdd/TEMPLATE.md", design_text())
    write(
        tmp_path,
        "docs/product/CONTRACTS.md",
        "## 1. Records\n\nRecords must survive retry.\n\n"
        "## 11. Configuration\n\n| Document | Schema |\n|---|---|\n| `words.json` | `words.schema.json` |\n\n"
        "## 18. Events\n\n| Type | Data |\n|---|---|\n| `record.changed` | Record |\n",
    )
    write(
        tmp_path,
        "docs/SYSTEM.md",
        "## 5. Kernel\n\n### 5.7 Events\n\n"
        "The domain event `RecordChanged` is published.\n\n### 5.11 Settings\n\n"
        "`hub-settings.json` holds bounds.\n\n## 8. Seams\n\n`RecordPort.get` reads a record.\n",
    )
    write(tmp_path, "docs/product/VALIDATION.md", "| V01 | Retry | E99 |\n| D01 | Demo | E99 |\n")
    write(tmp_path, "docs/product/PRD.md", "| R01 | Durable | E99 |\n")
    write_json(
        tmp_path,
        "docs/product/backlog.json",
        [{"epic_id": "E99", "story_id": "E99-01", "requirements": "R01", "acceptance": "V01 D01"}],
    )
    write_json(
        tmp_path,
        "docs/product/tool-catalog.json",
        {
            "tools": [
                {"name": "read_record", "input_schema": {"properties": {"record_id": {"type": "string"}}}}
            ]
        },
    )
    write_schemas(tmp_path)
    write_api(tmp_path)
    return dl.Package.load(tmp_path)


def write_schemas(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "docs/product/api/schemas/record.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Record",
            "type": "object",
            "properties": {
                "note": {"type": ["string", "null"]},
                "state": {"enum": ["open", "closed"]},
                "record_id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,15}$"},
            },
            "required": ["record_id", "state", "note"],
            "additionalProperties": False,
        },
    )
    write_json(
        tmp_path,
        "docs/product/api/schemas/event.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Event",
            "properties": {"type": {"enum": ["record.changed"]}},
        },
    )
    write_json(
        tmp_path,
        "docs/product/api/schemas/error.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "ErrorEnvelope",
            "properties": {"code": {"enum": ["BAD_RECORD", "NOT_ALLOWED"]}},
        },
    )


def write_api(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/product/api/openapi.yaml",
        """openapi: 3.1.0
paths:
  /v1/records/{record_id}:
    get:
      operationId: getRecord
      responses:
        '200':
          content:
            application/json:
              schema:
                $ref: schemas/record.schema.json
              examples:
                record:
                  $ref: examples/record.json
        '400':
          x-error-codes: [BAD_RECORD]
""",
    )
    write_json(
        tmp_path,
        "docs/product/api/examples/record.json",
        {"x-schema": "record.schema.json", "value": {"record_id": "r_one", "state": "open", "note": None}},
    )


def findings(package: dl.Package, changes: dict[str, str], check: int) -> list[dl.Finding]:
    doc = dl.Document.parse(package.root / "docs/tdd/E99.md", design_text(changes))
    return [f for f in dl.Linter(package).run([doc], [doc]) if f.check == check]


@pytest.mark.parametrize(
    ("check", "section", "bad", "good"),
    [
        (1, "6", "operationId: `missingOperation`", "operationId: `getRecord`"),
        (1, "6", "POST /v1/records/{record_id}", "GET /v1/records/{record_id}"),
        (2, "3", "`Record.missing`", "`Record.note`"),
        (2, "3", "Record.note: str", "Record.note: str | None"),
        (2, "5", "Record.note TEXT NOT NULL", "Record.note TEXT NULL"),
        (3, "3", 'Record.state: Literal["opne", "closed"]', 'Record.state: Literal["closed", "open"]'),
        (4, "4", "event `record.missing`", "event `record.changed`"),
        (4, "4", "event `MissingEvent`", "event `RecordChanged`"),
        (5, "8", "configuration document `mystery.json`", "configuration document `words.json`"),
        (6, "4", "tool `missing_tool`", "tool `read_record`"),
        (6, "4", "`read_record.bad_field`", "`read_record.record_id`"),
        (7, "6", "error code `BAD_UNKNOWN`", "error code `BAD_RECORD`"),
        (8, "3", 'Record.record_id = "bad-id"', 'Record.record_id = "r_one"'),
        (
            9,
            "2",
            "port signature `RecordPort.read() -> float`",
            "port signature `RecordPort.read() -> Quantity`",
        ),
        (12, "15", 'pytest -m "missing"', 'pytest -m "unit and not slow"'),
        (12, "12.1", "None", "| Requirement | Test ID |\n|---|---|\n| E99-01 R01 V01 D01 | T99-01 |"),
        (13, "14", "R-E99-7", "None"),
        (14, "1.1", "CONTRACTS.md:4", "CONTRACTS.md:3"),
    ],
)
def test_definite_mismatch_and_valid_case(
    package: dl.Package, check: int, section: str, bad: str, good: str
) -> None:
    assert any(f.severity == "error" for f in findings(package, {section: bad}, check))
    assert not findings(package, {section: good}, check)


def test_clean_fixture(package: dl.Package) -> None:
    assert findings(package, {}, 12) == []
    doc = dl.Document.parse(package.root / "docs/tdd/E99.md", design_text())
    assert dl.Linter(package).run([doc], [doc]) == []


def test_operation_responses(package: dl.Package) -> None:
    table = "| operationId | Errors |\n|---|---|\n| `getRecord` | {} |"
    assert findings(package, {"6": table.format("403 `NOT_ALLOWED`")}, 7)
    assert not findings(package, {"6": table.format("400 `BAD_RECORD`")}, 7)


def test_filed_request_and_existing_field(package: dl.Package) -> None:
    write(package.root, "docs/requests/E99.md", "## R-E99-7\nProposal.\n")
    assert not findings(package, {"14": "R-E99-7"}, 13)
    result = findings(package, {"14": "R-E99-7: Add Record.note."}, 13)
    assert result
    assert all(f.severity == "warning" for f in result)
    assert findings(package, {"14": "R-E99-70"}, 13)


def test_heuristics_never_block(package: dl.Package) -> None:
    result = findings(package, {"3": 'Local: Literal["opne"]'}, 3)
    assert result
    assert all(f.severity == "warning" for f in result)
    result = findings(package, {"3": "Record.record_id generated as `{a}_{b}` composite key"}, 8)
    assert result
    assert all(f.severity == "warning" for f in result)
    assert any("collision" in f.message for f in result)
    result = findings(package, {"14": "Never bypass dose validation."}, 15)
    assert result
    assert all(f.severity == "warning" for f in result)
    assert not findings(package, {"14": "The coordinator reviews the proposal."}, 15)


def seam_doc(package: dl.Package, epic: str, signature: str, provider: str = "E98") -> dl.Document:
    table = (
        "| Name | Provider | Consumers | Signature |\n|---|---|---|---|\n"
        f"| `RecordPort.get` | {provider} | E99 | `{signature}` |"
    )
    return dl.Document.parse(package.root / f"docs/tdd/{epic}.md", design_text({"2": table}))


@pytest.mark.parametrize("fault", ["missing", "duplicate", "signature", "wrong_provider"])
def test_seam_reconciliation(package: dl.Package, fault: str) -> None:
    consumer = seam_doc(package, "E99", "(id: str) -> Record")
    provider = seam_doc(package, "E98", "(id: str) -> Record")
    peers = [consumer, provider]
    good = dl.Linter(package)
    dl.reconcile(good, [consumer], peers)
    assert not good.findings
    if fault == "missing":
        peers = [consumer]
    elif fault == "signature":
        peers = [consumer, seam_doc(package, "E98", "(id: int) -> Record")]
    elif fault == "wrong_provider":
        peers = [consumer, seam_doc(package, "E97", "(id: str) -> Record", "E97")]
    else:
        peers.append(seam_doc(package, "E97", "(id: str) -> Record", "E97"))
    lint = dl.Linter(package)
    dl.reconcile(lint, [consumer], peers)
    assert any(f.check == 10 and f.severity == "error" for f in lint.findings)


def test_ownership_globs_and_read_only(package: dl.Package) -> None:
    left = dl.Document.parse(package.root / "docs/tdd/E99.md", design_text({"13": "`hub/shared/**`"}))
    right = dl.Document.parse(package.root / "docs/tdd/E98.md", design_text({"13": "`hub/shared/{a,b}.py`"}))
    lint = dl.Linter(package)
    dl.ownership(lint, [left], [left, right])
    assert len(lint.findings) == 2
    assert all(f.check == 11 and f.severity == "error" for f in lint.findings)
    right = dl.Document.parse(right.path, design_text({"13": "### 13.1 Not owned, used\n\n`hub/**`"}))
    lint = dl.Linter(package)
    dl.ownership(lint, [left], [left, right])
    assert not lint.findings
    assert dl.overlap("hub/*/a.py", "hub/one/two/a.py") is not True
    assert dl.overlap("hub/**/a.py", "hub/a.py") is True


def test_normative_wrapping_ranges_and_ownership(package: dl.Package) -> None:
    package.contracts = dl.Document.parse(
        package.contracts.path,
        "## 1. Records\n\nA record must\nsurvive retry. Records never vanish.\n"
        "\n## 2. Privacy\n\nOnly owners read it.\n",
    )
    result = findings(package, {"1.1": "CONTRACTS.md:3"}, 14)
    assert len(result) == 1
    assert "CONTRACTS.md:4" in result[0].message
    assert not findings(package, {"1.1": "CONTRACTS.md:3-4"}, 14)
    assert findings(package, {"1": "Owns CONTRACTS sections 1 to 2.", "1.1": "CONTRACTS.md:3-4"}, 14)
    assert not any(f.severity == "error" for f in findings(package, {"1": "Reads CONTRACTS section 2."}, 14))
    assert not dl.citation_lines("PRD.md:3 and SYSTEM.md:4")


def test_missing_sections_and_malformed_tables(package: dl.Package) -> None:
    doc = dl.Document.parse(package.root / "docs/tdd/E99.md", "# E99\n| broken |\n```\n## 2. Fake\n")
    result = dl.Linter(package).run([doc], [doc])
    assert result
    assert all(f.line >= 1 for f in result)
    assert any("Missing section 2" in f.message for f in result)


def test_units_do_not_accept_neighbor_quantity(package: dl.Package) -> None:
    assert findings(package, {"4": "port signature `Port.f(a: Quantity, b: float) -> None`"}, 9)
    assert not findings(package, {"3": "The local algorithm uses float arithmetic."}, 9)


def test_not_every_dotted_name_is_a_schema_field(package: dl.Package) -> None:
    assert not findings(package, {"7": "`Record.vue` renders it."}, 2)
    assert not findings(package, {"3": "`Record.save()` persists it."}, 2)
    result = findings(package, {"3": "class Record:\n    local_cache: str"}, 2)
    assert not any(f.severity == "error" for f in result)


def test_state_machine_events_are_not_wire_events(package: dl.Package) -> None:
    table = "| From | Event | To |\n|---|---|---|\n| waiting | `clicked` | done |"
    assert not findings(package, {"7": table}, 4)


def test_endpoint_parameter_names_and_incomplete_prose(package: dl.Package) -> None:
    assert not any(f.severity == "error" for f in findings(package, {"6": "GET /v1/records/{id}"}, 1))
    assert not any(
        f.severity == "error" for f in findings(package, {"6": "GET /v1/records/{another person's id}"}, 1)
    )


def test_package_valid_example_envelope(package: dl.Package) -> None:
    assert not dl.lint_package(package)


def test_package_invalid_example_and_placeholder(package: dl.Package) -> None:
    write_json(
        package.root,
        "docs/product/api/examples/record.json",
        {"x-schema": "record.schema.json", "value": {"record_id": "<id>", "state": "unknown", "note": None}},
    )
    result = dl.lint_package(package)
    assert any(f.severity == "error" and "pattern" in f.message for f in result)
    assert any(f.severity == "warning" and "placeholder" in f.message.lower() for f in result)
    assert all(f.line > 1 for f in result)


def test_package_consistency_is_heuristic(package: dl.Package) -> None:
    write_json(
        package.root,
        "docs/product/api/schemas/record-create.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "RecordCreate",
            "properties": {"note": {"type": "string"}, "state": {"enum": ["open", "pending"]}},
            "required": ["note"],
        },
    )
    changed = dl.Package.load(package.root)
    result = dl.package_fields(changed)
    assert any(f.check == 2 and "nullable" in f.message for f in result)
    assert any(f.check == 3 for f in result)
    assert all(f.severity == "warning" for f in result)


def test_jsonschema_absent_is_not_checked(package: dl.Package, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl, "HAS_JSONSCHEMA", False)
    result = dl.lint_package(package)
    assert result
    assert all(f.severity == "warning" and "not checked" in f.message for f in result)


def test_remote_reference_is_never_fetched(package: dl.Package) -> None:
    path = write_json(
        package.root,
        "docs/product/api/schemas/record.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Record",
            "$ref": "https://invalid.example/never-fetch.json",
        },
    )
    package.schemas = dl.Schemas.load(path.parent)
    result = dl.lint_package(package)
    assert result
    assert all(f.severity == "warning" and "not checked" in f.message for f in result)


def test_cli_json_deterministic_and_exit_codes(
    package: dl.Package, capsys: pytest.CaptureFixture[str]
) -> None:
    write(package.root, "docs/tdd/E99.md", design_text())
    assert dl.main(["--all", "--json"], root=package.root) == 0
    first = capsys.readouterr().out
    assert json.loads(first) == []
    assert dl.main(["docs/tdd/E99.md", "--json"], root=package.root) == 0
    assert capsys.readouterr().out == first
    write(package.root, "docs/tdd/E99.md", design_text({"6": "operationId: missingOperation"}))
    assert dl.main(["--all"], root=package.root) == 1
    output = capsys.readouterr().out
    assert "error: [check 1]" in output
    assert "check 15: 0 error(s), 0 warning(s)" in output
    assert dl.main(["--package", "--json"], root=package.root) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_real_designs_complete(capsys: pytest.CaptureFixture[str]) -> None:
    root = Path(__file__).resolve().parents[2]
    assert dl.main(["--all", "--json"], root=root) in (0, 1)
    result = json.loads(capsys.readouterr().out)
    assert isinstance(result, list)
    assert not any("Input could not be read" in row["message"] for row in result)


def test_local_float_helpers_are_not_ports(package: dl.Package) -> None:
    assert not findings(package, {"3": "def interpolate(a: float) -> float: ..."}, 9)
    result = findings(package, {"4": "class Reader(Protocol):\n    def read(self) -> float: ..."}, 9)
    assert any(f.severity == "error" for f in result)


def test_python_module_option_is_not_a_marker(package: dl.Package) -> None:
    assert not findings(package, {"15": "python -m pytest tests/"}, 12)
    assert findings(package, {"15": 'python -m pytest tests/ -m "missing"'}, 12)


def test_contract_citations_are_source_bound(package: dl.Package) -> None:
    assert dl.citation_lines("SYSTEM.md#L3 and PRD.md#L4") == set()
    result = findings(package, {"1.1": "SYSTEM.md#L3"}, 14)
    assert any(f.severity == "error" for f in result)
    assert dl.citation_lines("CONTRACTS.md#L3-L4") == {3, 4}


def test_schema_without_dialect_is_validated(package: dl.Package) -> None:
    write_json(package.root, "docs/product/api/schemas/no-dialect.json", {"type": "string"})
    package.schemas = dl.Schemas.load(package.root / "docs/product/api/schemas")
    assert not dl.lint_package(package)


def test_nullability_siblings_and_cycles(package: dl.Package) -> None:
    path = package.root / "docs/product/api/schemas/record.schema.json"
    schema = dl.Schema(path, (), {"type": "string", "anyOf": [{"type": "string"}, {"type": "null"}]})
    assert package.schemas.nullable(schema) is False
    write_json(
        package.root,
        "docs/product/api/schemas/cycle.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Cycle",
            "anyOf": [{"$ref": "#"}, {"type": "null"}],
        },
    )
    package.schemas = dl.Schemas.load(path.parent)
    assert package.schemas.nullable(package.schemas.names["Cycle"]) is None


def test_ownership_language_is_not_an_unrelated_own_word(package: dl.Package) -> None:
    doc = dl.Document.parse(
        package.root / "docs/tdd/E99.md",
        design_text(
            {"1": "CONTRACTS 2: every record has its own owner.\n| Decision | own authority | CONTRACTS 3 |"}
        ),
    )
    assert dl.owned_sections(doc) == set()


def test_config_example_envelopes_and_settings(package: dl.Package) -> None:
    schema_path = package.root / "docs/product/api/schemas/record.schema.json"
    write(package.root, "docs/product/api/schemas/config-words.schema.json", schema_path.read_text())
    write_json(
        package.root,
        "docs/product/api/examples/config/words.example.json",
        {
            "summary": "Fixture",
            "x-schema": "config-words.schema.json",
            "value": {"record_id": "r_one", "state": "open", "note": None},
        },
    )
    package.schemas = dl.Schemas.load(schema_path.parent)
    assert not dl.lint_package(package)
    table = "| Document | Fields |\n|---|---|\n| `worker/settings.json` | `timeout` |"
    assert not any(f.severity == "error" for f in findings(package, {"8": table}, 5))


def test_ownership_explanations_are_not_declarations(package: dl.Package) -> None:
    left = dl.Document.parse(
        package.root / "docs/tdd/E99.md",
        design_text(
            {
                "13": "- `hub/one/**` (`domain/`, `adapters/`); E98 owns `hub/two/**`\n"
                "\nNothing else under `hub/` is owned. Not owned: E98's\n`hub/two/**`."
            }
        ),
    )
    right = dl.Document.parse(
        package.root / "docs/tdd/E98.md", design_text({"13": "- `hub/two/**` (`domain/`, `adapters/`)"})
    )
    lint = dl.Linter(package)
    dl.ownership(lint, [left], [left, right])
    assert not lint.findings


def test_event_fields_and_schema_metapaths(package: dl.Package) -> None:
    assert not findings(package, {"3": "The event `seq` orders delivery."}, 4)
    assert not findings(package, {"3": "`Record.properties.note`"}, 2)


def test_catalog_edit_changes_lint_without_code_change(package: dl.Package) -> None:
    assert findings(package, {"4": "tool `new_reader`"}, 6)
    write_json(package.root, "docs/product/tool-catalog.json", {"tools": [{"name": "new_reader"}]})
    assert not findings(dl.Package.load(package.root), {"4": "tool `new_reader`"}, 6)


def test_ownership_multiline_parentheses(package: dl.Package) -> None:
    left = dl.Document.parse(
        package.root / "docs/tdd/E99.md",
        design_text(
            {
                "13": "```\nhub/src/one/** (domain,\n   adapters/migrations/one.sql)\n```\n"
                "- `hub/src/two/**` (`domain/`,\n  `adapters/`), `hub/tests/two/**`"
            }
        ),
    )
    paths = {owned.pattern for owned in dl.read_owned(left)}
    assert paths == {"hub/src/one/**", "hub/src/two/**", "hub/tests/two/**"}


def test_multiline_other_owner_clause(package: dl.Package) -> None:
    doc = dl.Document.parse(
        package.root / "docs/tdd/E99.md",
        design_text(
            {
                "13": "- `knowledge/workbench/**`; E98 owns `knowledge/policy/**` and\n"
                "  `knowledge/schemas/**`, so the sets are disjoint\n- `hub/tests/workbench/**`"
            }
        ),
    )
    assert {p.pattern for p in dl.read_owned(doc)} == {"knowledge/workbench/**", "hub/tests/workbench/**"}


def test_numeric_literal_enums_are_compared_by_value(package: dl.Package) -> None:
    schema = package.schemas.names["Record"]
    dl.obj(dl.obj(schema.data["properties"])["state"])["enum"] = [1, 2]
    assert not findings(package, {"3": "Record.state: Literal[1, 2]"}, 3)
    assert findings(package, {"3": "Record.state: Literal[1, 3]"}, 3)
    assert findings(package, {"3": 'Record.state: Literal["1", "2"]'}, 3)


def test_epic_table_rows_credit_only_their_own_epic(package: dl.Package) -> None:
    prd = package.root / "docs/product/PRD.md"
    prd.write_text(prd.read_text() + "| E98 Other epic | B | S2 | E01, E99 | R07, R36 |\n", encoding="utf-8")
    reloaded = dl.Package.load(package.root)
    assert {"R07", "R36"} <= reloaded.requirements["E98"]
    assert not {"R07", "R36"} & reloaded.requirements.get("E99", set())
    assert "R01" in reloaded.requirements["E99"]


def test_bare_children_of_a_parent_glob_resolve_under_it(package: dl.Package) -> None:
    peer = dl.Document.parse(
        package.root / "docs/tdd/E98.md",
        design_text(
            {
                "13": "- `hub/src/x/workbench/**`: `domain/` (`a.py`,\n  `b.py`), `application/` (`c.py`,\n"
                "  `d.py`), `adapters/` (`exa.py`,\n  `disk.py`)\n- `hub/tests/workbench/**`"
            }
        ),
    )
    assert {p.pattern for p in dl.read_owned(peer)} == {
        "hub/src/x/workbench/**",
        "hub/src/x/workbench/domain/",
        "hub/src/x/workbench/application/",
        "hub/src/x/workbench/adapters/",
        "hub/tests/workbench/**",
    }
    own = dl.Document.parse(package.root / "docs/tdd/E99.md", design_text({"13": "- `adapters/habitat/**`"}))
    lint = dl.Linter(package)
    dl.ownership(lint, [own], [own, peer])
    assert not lint.findings
