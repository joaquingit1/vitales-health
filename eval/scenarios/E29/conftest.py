"""Allow the real help harness under pytest's importlib mode."""

import sys
from pathlib import Path

_HELP = str(Path(__file__).resolve().parents[3] / "hub/tests/help")
if _HELP not in sys.path:
    sys.path.insert(0, _HELP)

_TESTS = str(Path(__file__).resolve().parents[3] / "hub/tests")
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)
