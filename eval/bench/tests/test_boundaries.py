"""T-E18-17, 24, 25, 31: invalid inputs cannot weaken evaluation."""

from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_bench.adapters.documents import BenchSettings, load_document, read_json
from crew_health_bench.application.gate import validate_gate_configuration
from crew_health_bench.domain.cases import ConfigPrecondition, validate_cases
from crew_health_bench.domain.checks import NoCanaryLeak, NoValueForCode, ToolCallsWithin
from crew_health_bench.domain.claims import ClaimsPolicy, lint_claims
from crew_health_bench.domain.documents import integer, json_pointer, number, object_map, text, text_list
from crew_health_bench.domain.errors import BenchError
from crew_health_bench.domain.labels import check_preconditions
from crew_health_bench.domain.metrics import category_metrics, compare
from crew_health_bench.domain.partition import PartitionPolicy
from crew_health_bench.domain.results import benchmark_labels, scenario_outcome, status
from crew_health_bench.domain.sample import eligible_order, sample_size
from crew_health_bench.domain.text import jaccard, shingles

pytestmark = [pytest.mark.e18, pytest.mark.trace("R22", "R23", "R30", "INV-E18-12")]
CONFIG = Path(__file__).parents[1] / "config"


@given(st.one_of(st.none(), st.booleans(), st.lists(st.integers(), max_size=10)))
def test_boundary_parsers_never_coerce(value: object) -> None:
    for parser in (integer, object_map, text):
        with pytest.raises(BenchError):
            parser(value)


def test_boundary_text_list_and_pointer() -> None:
    assert text_list(["label"]) == ("label",)
    assert json_pointer({"doc": {"a/b": ["value"]}}, "doc", "/a~1b/0") == "value"
    assert json_pointer({"doc": None}, "doc", "") is None
    for document, pointer in (("missing", ""), ("doc", "invalid"), ("doc", "/missing"), ("doc", "/list/2")):
        with pytest.raises(BenchError):
            json_pointer({"doc": {"list": []}}, document, pointer)
    with pytest.raises(BenchError):
        object_map({1: "value"})
    with pytest.raises(BenchError):
        text_list(None)
    for value in (False, None, float("inf"), Decimal("NaN")):
        with pytest.raises(BenchError):
            number(value)


@pytest.mark.parametrize("data", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b"{", b"[]"])
def test_t_e18_17_bad_json_rejected(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "document.json"
    path.write_bytes(data)
    with pytest.raises(BenchError):
        read_json(path, max_file_bytes=100)


def test_t_e18_17_oversized_document_and_bad_schema(tmp_path: Path) -> None:
    path = tmp_path / "document.json"
    path.write_text('{"a":1}')
    with pytest.raises(BenchError):
        read_json(path, max_file_bytes=1)
    with pytest.raises(BenchError):
        read_json(path, max_file_bytes=0)
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas/document.schema.json").write_text('{"type":"invalid_type"}')
    with pytest.raises(BenchError):
        load_document(path, max_file_bytes=100)


def test_t_e18_17_gate_configuration_never_weakens() -> None:
    """INV-E18-12: each of the four gates allows zero failures of exactly its own check; nothing can loosen
    one."""
    document = load_document(CONFIG / "suites.json", max_file_bytes=16777216)
    gates = object_map(document["release_gates"])
    validate_gate_configuration(gates)
    invalid: list[dict[str, object]] = [{}, {key: gates[key] for key in list(gates)[1:]}]
    invalid.append({**gates, "extra_gate": {"max_count": 0, "checks": ["NoCrossPerson"]}})
    for name in gates:
        row = object_map(gates[name])
        for replacement in (
            None,
            {**row, "max_count": 1},
            {**row, "max_count": -1},
            {**row, "max_count": False},
            {**row, "max_count": 0.0},
            {**row, "checks": []},
            {**row, "checks": [*row["checks"], "NoCanaryLeak"]},  # type: ignore[misc]
            {**row, "checks": ["UrgencyAtLeast"]},
            {"checks": row["checks"]},
        ):
            invalid.append({**gates, name: replacement})
    for configuration in invalid:
        with pytest.raises(BenchError, match="^gate_input_invalid$"):
            validate_gate_configuration(configuration)


def test_t_e18_09_publisher_score_rule_from_configuration() -> None:
    policy = ClaimsPolicy("configured", (), 5, (("candidate", "0.125"),))
    assert lint_claims("candidate scored 0.125", policy)
    assert not lint_claims("candidate unmeasured", policy)


def test_settings_types_and_unknown_keys() -> None:
    settings = BenchSettings(load_document(CONFIG / "bench-settings.json", max_file_bytes=16777216))
    assert settings.integer("bench.grader.max_retries") == 3
    assert settings.quantity("bench.grader.call_timeout").unit.code == "s"
    for read in (settings.integer, settings.quantity, settings.boolean, settings.text):
        with pytest.raises(BenchError):
            read("missing")
    with pytest.raises(BenchError):
        settings.boolean("bench.grader.max_retries")
    with pytest.raises(BenchError):
        settings.text("bench.grader.max_retries")


def test_t_e18_24_empty_patterns_and_negative_tool_bound() -> None:
    for canaries in ((), ("",)):
        with pytest.raises(BenchError):
            NoCanaryLeak(canaries)
    for code, patterns in (("", ("a",)), ("code", ()), ("code", ("",)), ("code", ("[",))):
        with pytest.raises(BenchError):
            NoValueForCode(code, patterns)
    with pytest.raises(BenchError):
        ToolCallsWithin((), -1)


def test_t_e18_13_duplicate_ids_and_impossible_sample() -> None:
    from crew_health.kernel.units import Quantity, Unit

    with pytest.raises(BenchError):
        eligible_order(("duplicate", "duplicate"), seed="1", frame_exclusions=())
    with pytest.raises(BenchError):
        sample_size(-1, Quantity(Decimal(1), Unit("s")), Quantity(Decimal(1), Unit("s")))
    assert jaccard(frozenset(), frozenset()) == 0
    assert shingles("short", 5) == frozenset({"short"})
    assert shingles("", 5) == frozenset()
    with pytest.raises(ValueError):
        shingles("", 0)


def test_result_boundary_rejection() -> None:
    with pytest.raises(BenchError):
        status("planned", "full")
    with pytest.raises(BenchError):
        scenario_outcome("invented", ())
    with pytest.raises(BenchError):
        scenario_outcome("completed", ("invented",))
    for completeness, integrity, n, N, grader in (
        ("full", "official", 0, 1, "grader"),
        ("full", "invented", 1, 1, "grader"),
        ("invented", "official", 1, 1, "grader"),
        ("full", "official", 1, 1, ""),
    ):
        with pytest.raises(BenchError):
            benchmark_labels(
                track="model_only",
                variant="main",
                completeness=completeness,
                integrity=integrity,
                n=n,
                N=N,
                grader=grader,
                benchmark_disclaimer="configured",
            )
    for outcomes, n_cases in ((("invalid",), 1), (("pass",), 0)):
        with pytest.raises(BenchError):
            category_metrics(outcomes, n_cases=n_cases)
    with pytest.raises(BenchError):
        compare(
            (float("inf"),),
            (0.0,),
            case_set_a="same",
            case_set_b="same",
            baseline_gates={},
            candidate_gates={},
            seed=1,
            bootstrap_b=10,
            interval_level=0.95,
        )


def test_t_e18_31_null_precondition_and_empty_cases() -> None:
    assert check_preconditions((ConfigPrecondition("doc", "/value", "null"),), {"doc": {"value": None}})
    assert not check_preconditions((ConfigPrecondition("doc", "/value", "null"),), {"doc": {"value": 1}})
    assert not check_preconditions((ConfigPrecondition("doc", "/value", "set"),), {"doc": {"value": None}})
    both = (ConfigPrecondition("doc", "/value", "set"), ConfigPrecondition("doc", "/other", "null"))
    assert check_preconditions(both, {"doc": {"value": 1, "other": None}})
    assert not check_preconditions(both, {"doc": {"value": 1, "other": 2}})
    validate_cases(())
    with pytest.raises(BenchError):
        PartitionPolicy((), 1, 1, 1, Decimal("0.5"), Decimal("0.3"), 2)


@pytest.mark.trace("R22", "invariant")
def test_finite_json_syntax_cannot_overflow_into_an_infinite_quantity() -> None:
    from crew_health_bench.adapters.documents import decode_json

    with pytest.raises(BenchError, match="case_invalid"):
        decode_json(b'{"value":1e500,"unit":"s"}')
