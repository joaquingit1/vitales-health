"""Hypothesis profile for the property tests (spec 15.3): derandomized, at least 200
examples per property, no deadline, and no example database (three builders share the
working tree, so nothing is written to .hypothesis)."""

from hypothesis import HealthCheck, settings

settings.register_profile(
    "spec-15-3",
    derandomize=True,
    max_examples=200,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("spec-15-3")
