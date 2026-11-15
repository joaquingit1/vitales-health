import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.assistant.application.json_data import JsonBounds, parse
from crew_health.kernel.errors import ProblemError

BOUNDS = JsonBounds(8192, 8, 100, 20, 100)


@pytest.mark.trace("T-E13-011", "INV-E13-11", "R17")
@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1e999}',
        b"9007199254740992",
        b"[[[[[[[[[0]]]]]]]]]",
        b'"' + b"x" * 101 + b'"',
        b"[" + b"0," * 20 + b"0]",
    ],
)
def test_untrusted_json_refuses_duplicate_nonfinite_overflow_and_each_bound(raw: bytes) -> None:
    with pytest.raises(ProblemError):
        parse(raw, BOUNDS)


@pytest.mark.trace("T-E13-021", "INV-E13-11")
def test_total_node_and_byte_budgets_apply_before_model_or_provider_use() -> None:
    raw = json.dumps([[0] * 20 for _ in range(6)]).encode()
    with pytest.raises(ProblemError):
        parse(raw, BOUNDS)
    with pytest.raises(ProblemError):
        parse(b" " * 8193, BOUNDS)


@pytest.mark.trace("T-E13-021", "R17")
@given(
    value=st.dictionaries(
        st.text(alphabet="abc", max_size=5),
        st.one_of(
            st.none(),
            st.booleans(),
            st.integers(min_value=-1000, max_value=1000),
            st.text(alphabet="abc", max_size=20),
        ),
        max_size=10,
    )
)
def test_bounded_json_preserves_actual_zero_false_and_explicit_missing_values(
    value: dict[str, object],
) -> None:
    assert parse(json.dumps(value).encode(), BOUNDS) == value
    assert parse(b'{"zero":0,"false":false,"missing":null}', BOUNDS) == {
        "zero": 0,
        "false": False,
        "missing": None,
    }
