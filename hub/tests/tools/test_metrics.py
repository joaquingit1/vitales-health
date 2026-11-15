from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.tools.domain.context import require_context_budget
from crew_health.tools.domain.errors import ToolsInvalid


@pytest.mark.trace("T-E12-037", "T-E12-043", "I4")
@given(st.integers(min_value=0, max_value=40000), st.integers(min_value=0, max_value=40000))
def test_full_prompt_and_output_reserve_must_fit(tokens: int, reserve: int) -> None:
    if tokens + reserve <= 32768:
        require_context_budget(
            prompt=b"{}", prompt_tokens=tokens, output_reserve=reserve, max_bytes=262144, max_tokens=32768
        )
    else:
        with pytest.raises(ToolsInvalid):
            require_context_budget(
                prompt=b"{}", prompt_tokens=tokens, output_reserve=reserve, max_bytes=262144, max_tokens=32768
            )


@pytest.mark.trace("T-E12-034", "T-E12-043", "I4", "I7")
def test_missing_tokenizer_never_uses_bytes_as_tokens() -> None:
    with pytest.raises(ToolsInvalid):
        require_context_budget(
            prompt=b"{}", prompt_tokens=None, output_reserve=1, max_bytes=262144, max_tokens=32768
        )


@pytest.mark.trace("T-E12-037", "T-E12-043", "I4")
def test_required_facts_are_never_truncated_to_fit() -> None:
    prompt = b'{"red_flags":[true],"contradictions":[true],"negatives":[true]}'
    with pytest.raises(ToolsInvalid):
        require_context_budget(
            prompt=prompt, prompt_tokens=30, output_reserve=1, max_bytes=len(prompt) - 1, max_tokens=32768
        )
    assert prompt == b'{"red_flags":[true],"contradictions":[true],"negatives":[true]}'


@pytest.mark.trace("T-E12-005", "T-E12-043", "I4")
@pytest.mark.parametrize(
    ("tokens", "reserve", "max_bytes", "max_tokens"),
    [
        (1, 1, 0, 32768),
        (-1, 1, 262144, 32768),
        (1, -1, 262144, 32768),
        (1, 1, 262144, 0),
    ],
)
def test_invalid_prompt_budget_cannot_authorize_inference(
    tokens: int, reserve: int, max_bytes: int, max_tokens: int
) -> None:
    with pytest.raises(ToolsInvalid):
        require_context_budget(
            prompt=b"{}",
            prompt_tokens=tokens,
            output_reserve=reserve,
            max_bytes=max_bytes,
            max_tokens=max_tokens,
        )
