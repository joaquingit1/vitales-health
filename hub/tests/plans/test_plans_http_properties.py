"""Contract-bound headers accept their whole declared range and reject malformed input."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from starlette.requests import Request

from crew_health.kernel.errors import ProblemError
from crew_health.plans.api.router import key, revision


def request(header: str, value: str) -> Request:
    return Request({"type": "http", "headers": [(header.encode("ascii"), value.encode("ascii"))]})


@pytest.mark.trace("T28-29", "SYSTEM-5.8")
@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=150))
def test_idempotency_header_preserves_valid_keys_and_refuses_both_bounds(value: str) -> None:
    req = request("idempotency-key", value)
    if 8 <= len(value) <= 128:
        assert key(req) == value
    else:
        with pytest.raises(ProblemError):
            key(req)


@pytest.mark.trace("T28-29", "T28-10")
@given(st.integers(min_value=0, max_value=2**63 - 1), st.booleans())
def test_if_match_round_trips_the_full_integer_range(value: int, quoted: bool) -> None:
    encoded = '"' + str(value) + '"' if quoted else str(value)
    assert revision(request("if-match", encoded)) == value
