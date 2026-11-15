"""Validate episode rules before passing them to a host composition's pinned source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from crew_health.episodes.application.rules import parse_rules
from crew_health.kernel.errors import ProblemError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("verify-rules",))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        with args.path.open("rb") as stream:
            raw = stream.read(1048577)
        rules = parse_rules(raw)
    except OSError, ValueError, ProblemError:
        print(json.dumps({"valid": False}))
        return 1
    print(json.dumps({"valid": True, "sha256": rules.version, "policies": sorted(rules.task_policies)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
