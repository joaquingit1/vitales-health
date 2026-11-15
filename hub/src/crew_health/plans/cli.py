"""Standalone dissimilar backup: python -m crew_health.plans.cli print-cards."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.plans.cards import write_cards
from crew_health.plans.domain.errors import PlansError


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, "invalid print command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = Parser(prog="crew-health-plans")
    parser.add_argument("command", choices=("print-cards",))
    parser.add_argument("--documents", required=True, type=Path)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--out", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        write_cards(args.documents, args.environment, args.out)
        return 0
    except (ProblemError, PlansError, OSError) as error:
        code = error.code if isinstance(error, ProblemError) else ErrorCode.CONFIG_INVALID
        print(json.dumps({"code": code.value}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
