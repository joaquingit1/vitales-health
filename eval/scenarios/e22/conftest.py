"""The E22 scenario reuses the served E02 station and the participation harness of the hub tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HUB_TESTS = Path(__file__).resolve().parents[3] / "hub/tests"
for _path in (_HUB_TESTS / "privacy", _HUB_TESTS / "participation"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "E22: participation streak, patches and crew goal")
