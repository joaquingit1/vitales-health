"""V75: edit the release document, keeping HTTP records and resolved services intact.

The E15 EpisodeReader fake supplies a complete empty set of episode periods.
"""

from pathlib import Path

import pytest
from crew_health.trends.application.ports import BaselineSummary, InsufficientBaseline

from .support import ScenarioHub, edited_catalog, history

pytestmark = pytest.mark.trace("V75", "AGENTS-1.1")


def test_catalog_window_and_minimum_change_the_served_usual(tmp_path: Path) -> None:
    hub = ScenarioHub(tmp_path)
    history(hub)
    original = hub.read()
    changed_release = edited_catalog(window=3, minimum=2)
    hub.station.db.set_release_provider(lambda: changed_release)
    changed = hub.read()
    assert isinstance(original.usual, BaselineSummary)
    assert original.usual.value.value == 30
    assert isinstance(changed.usual, BaselineSummary)
    assert changed.usual.value.value == 45
    assert changed.usual.window_days == 3
    assert changed.usual.min_samples == 2
    assert original.usual.catalog_version != changed.usual.catalog_version
    insufficient_release = edited_catalog(window=3, minimum=3)
    hub.station.db.set_release_provider(lambda: insufficient_release)
    insufficient = hub.read()
    assert isinstance(insufficient.usual, InsufficientBaseline)
    assert insufficient.usual.sample_count == 2
    assert insufficient.usual.min_samples == 3
    assert insufficient.state == "no_baseline"
    assert insufficient.today == changed.today == original.today
