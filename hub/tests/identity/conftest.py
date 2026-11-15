"""Makes the shared E02 station in hub/tests/privacy importable under pytest's importlib mode."""

from __future__ import annotations

import sys
from pathlib import Path

_SHARED = str(Path(__file__).resolve().parents[1] / "privacy")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)
