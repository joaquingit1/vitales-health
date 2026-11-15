"""The real E23/E24 station of hub/tests, for the mission-day scenario of E24 (T-E24-110)."""

import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[3] / "hub/tests"
for _name in ("sensors", "rules"):
    _path = str(_TESTS / _name)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e24_station import patched  # noqa: E402, F401  (the station fixture)


def pytest_configure(config: object) -> None:
    config.addinivalue_line("markers", "E24: warnings engine")  # type: ignore[attr-defined]
