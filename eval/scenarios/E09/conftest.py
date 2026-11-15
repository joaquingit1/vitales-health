"""Puts this directory on sys.path so `support` imports as a top-level module (E09 is not a package name)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
