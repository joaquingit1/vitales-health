"""Puts the E09 harness on the import path (importlib import mode has no package-relative imports)."""

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_INPUTS = str(Path(__file__).resolve().parents[3] / "eval" / "scenarios" / "E09" / "inputs")
if _INPUTS not in sys.path:
    sys.path.insert(0, _INPUTS)
