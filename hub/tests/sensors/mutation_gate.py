"""Mutation score gate for the sensor domain (E23 15, tasks.json T2 gates).

Run after `uv run --project hub mutmut run --paths-to-mutate hub/src/crew_health/sensors/domain`. It reads the
statistics mutmut writes (`mutants/mutmut-cicd-stats.json`) and exits non zero when the score is below the
minimum. Surviving mutants are never filtered or suppressed here; a missing statistics file fails the gate.

This gate has not run in this checkout: `mutmut` is not a hub dependency (an E01 request adds it).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_STATS = Path("mutants/mutmut-cicd-stats.json")
KILLED_KEYS = ("killed", "timeout", "segfault")
SURVIVED_KEYS = ("survived", "suspicious", "no_tests")
EXIT_BELOW_MINIMUM = 1
EXIT_NO_STATISTICS = 2


def score(stats: dict[str, int]) -> tuple[float, int, int]:
    """Killed over killed plus surviving, counting suspicious and untested mutants as surviving."""
    killed = sum(int(stats.get(key, 0)) for key in KILLED_KEYS)
    survived = sum(int(stats.get(key, 0)) for key in SURVIVED_KEYS)
    total = killed + survived
    if total == 0:
        return 0.0, killed, survived
    return killed / total, killed, survived


def load(path: Path) -> dict[str, int] | None:
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return None
    return {str(key): int(value) for key, value in loaded.items() if isinstance(value, int)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=float, required=True)
    parser.add_argument("--stats", type=Path, default=DEFAULT_STATS)
    args = parser.parse_args(argv)
    if not 0.0 <= args.min_score <= 1.0:
        parser.error("--min-score must be between 0 and 1")
    stats = load(args.stats)
    if stats is None:
        print(f"mutation gate: no statistics at {args.stats}; run mutmut first", file=sys.stderr)
        return EXIT_NO_STATISTICS
    value, killed, survived = score(stats)
    print(f"mutation score {value:.4f} (killed {killed}, surviving {survived}, minimum {args.min_score})")
    return EXIT_BELOW_MINIMUM if killed + survived == 0 or value < args.min_score else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
