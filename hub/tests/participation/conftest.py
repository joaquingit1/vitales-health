from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The served E02 station (real identity, clock, config and HTTP over SQLite) lives with the privacy tests.
_PRIVACY = str(Path(__file__).resolve().parents[1] / "privacy")
for _path in (_PRIVACY, str(Path(__file__).resolve().parent)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "E22: participation streak, patches and crew goal")
    config.addinivalue_line("markers", "property: Hypothesis property tests")
