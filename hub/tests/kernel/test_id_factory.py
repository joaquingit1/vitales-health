"""SYSTEM 5.1: an I/O-free factory usable across the evaluation boundary."""

from hashlib import sha256

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import KernelIdFactory

pytestmark = [pytest.mark.E01, pytest.mark.trace("SYSTEM-5.1", "R22")]


@given(st.text(max_size=40), st.text(max_size=40))
def test_derived_ids_use_the_shared_canonical_array(a: str, b: str) -> None:
    factory = KernelIdFactory(new_uuid_hex=lambda: "1234")
    expected = sha256(canonical_json(["benchrun", a, b])).hexdigest()[:32]
    assert factory.derived("benchrun", a, b) == "benchrun_" + expected


def test_factory_delegates_entropy_and_rejects_invalid_complete_ids() -> None:
    factory = KernelIdFactory(new_uuid_hex=lambda: "0123456789abcdef")
    assert factory.new("benchcanary") == "benchcanary_0123456789abcdef"
    for prefix in ("", "../escape", "x" * 128):
        with pytest.raises(ProblemError):
            factory.new(prefix)
        with pytest.raises(ProblemError):
            factory.derived(prefix, "input")
