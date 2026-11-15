"""Expose the hub's tests package (hub/tests/tools doubles) and this directory under pytest's importlib
mode."""

import sys
from pathlib import Path

for _path in (Path(__file__).resolve().parent, Path(__file__).resolve().parents[3] / "hub"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
