"""Puts this directory and inputs/ on sys.path so `support` and `builders` import (E09 is no package name)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "inputs"))
