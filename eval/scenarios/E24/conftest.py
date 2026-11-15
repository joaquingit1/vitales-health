"""The real E23/E24 station of hub/tests, for the mission-day scenario of E24 (T-E24-110)."""

import sys
from pathlib import Path

_TESTS = str(Path(__file__).resolve().parents[3] / "hub/tests")
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)  # the hub/tests/rules package: its station helpers import package-relatively

from rules.e24_station import patched  # noqa: E402, F401  (the station fixture)


def pytest_configure(config: object) -> None:
    config.addinivalue_line("markers", "E24: warnings engine")  # type: ignore[attr-defined]
