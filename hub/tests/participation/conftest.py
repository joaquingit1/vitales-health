from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "E22: participation streak, patches and crew goal")
    config.addinivalue_line("markers", "property: Hypothesis property tests")
