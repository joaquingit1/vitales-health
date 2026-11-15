"""Command line recording generator."""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from .scenarios import load_scenarios
from .twin import Twin, recording


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    record = commands.add_parser("record")
    record.add_argument("--family", choices=("oximeter", "cuff", "thermometer"), required=True)
    record.add_argument("--scenario", choices=tuple(load_scenarios()), required=True)
    record.add_argument("--seed", type=int, required=True)
    record.add_argument("--count", type=int, required=True)
    record.add_argument("--start", default="2000-01-01T00:00:00+00:00")
    record.add_argument("--interval-s", default="1")
    args = parser.parse_args(argv)
    if args.command == "list":
        for scenario in load_scenarios().values():
            print(f"{scenario.name}: {scenario.description}")
        return
    try:
        start = datetime.fromisoformat(args.start)
        if start.tzinfo is not None:
            start = start.astimezone(UTC)
        twin = Twin(args.family, args.scenario, args.seed, start=start, interval_s=Decimal(args.interval_s))
        print(json.dumps(recording(twin, args.count)))
    except (ValueError, InvalidOperation) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
