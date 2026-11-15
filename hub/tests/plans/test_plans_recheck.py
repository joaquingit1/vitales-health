"""Injected-time recheck arithmetic and pending words."""

from datetime import timedelta

import pytest

from crew_health.plans.domain.model import RecheckSpec
from crew_health.plans.domain.recheck import due_at, due_words, recheck_shape_ok

from .harness import now


@pytest.mark.trace("T28-14", "R57")
def test_due_shapes_and_words() -> None:
    cases = (
        (None, None, 60, None, 60),
        ("daily", None, None, None, 86400),
        ("weekly", None, None, None, 604800),
        ("every_n_days", 3, None, None, 259200),
        ("monthly", None, None, None, 30 * 86400),
        (None, None, None, "clinician", None),
    )
    for cadence, n, interval, setter, seconds in cases:
        spec = RecheckSpec(cadence, n, interval, "sourced", setter)
        due = None if seconds is None else now() + timedelta(seconds=seconds)
        assert due_at(spec, now(), month_days=30) == due
        assert due_at(spec, None, month_days=30) is None
        assert due_words(spec, due) == (("pending", setter) if setter else ("at", None))
        assert recheck_shape_ok(spec)
    for cadence in ("continuous", "per_eva", "after_event", "mission_phase", "as_needed"):
        spec = RecheckSpec(cadence, None, None, "sourced", None)
        assert due_at(spec, now(), month_days=None) is None
        assert due_words(spec, None) == ("cadence", cadence)
    for spec in (
        RecheckSpec("daily", None, 60, "sourced", None),
        RecheckSpec(None, None, None, "sourced", None),
        RecheckSpec("every_n_days", None, None, "sourced", None),
        RecheckSpec("every_n_days", 0, None, "sourced", None),
    ):
        assert not recheck_shape_ok(spec)
    assert due_at(RecheckSpec("monthly", None, None, "sourced", None), now(), month_days=None) is None
    assert due_at(RecheckSpec("every_n_days", None, None, "sourced", None), now(), month_days=30) is None
    assert due_words(RecheckSpec(None, None, 60, "sourced", None), None) == ("cadence", None)
