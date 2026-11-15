"""Properties for revision and HTTP page boundaries."""

from __future__ import annotations

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from starlette.requests import Request

from crew_health.kernel.errors import ProblemError
from crew_health.routine.api.router import _if_match, _page


def request(*, revision: str = "", query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(b"if-match", revision.encode("ascii"))],
            "query_string": query.encode("ascii"),
        }
    )


@given(st.integers(min_value=0, max_value=2**63 - 1), st.booleans())
@pytest.mark.trace("T-E25-018")
def test_etag_revision_round_trips_for_bare_or_quoted_decimal(value: int, quoted: bool) -> None:
    text = str(value)
    assert _if_match(request(revision='"' + text + '"' if quoted else text)) == value


@example(limit=0)
@example(limit=1)
@example(limit=200)
@example(limit=201)
@given(st.integers(min_value=-1000, max_value=1000))
@pytest.mark.trace("T-E25-015")
def test_http_limit_accepts_exactly_the_contract_interval(limit: int) -> None:
    if 1 <= limit <= 200:
        assert _page(request(query=f"limit={limit}")) == (None, limit)
    else:
        with pytest.raises(ProblemError):
            _page(request(query=f"limit={limit}"))
