"""Expose the E05-style scenario harness under pytest's importlib mode."""

import sys
from pathlib import Path

_DIRECTORY = str(Path(__file__).resolve().parent)
if _DIRECTORY not in sys.path:
    sys.path.insert(0, _DIRECTORY)
