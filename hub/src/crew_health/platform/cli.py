from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.platform.domain.errors import fail
from crew_health.platform.storage import inspect_sqlite


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise fail(ErrorCode.MALFORMED_REQUEST)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(prog="crew-health")
    parser.add_argument("command", choices=("doctor", "verify-store", "serve", "backup", "restore"))
    parser.add_argument("--database", type=Path)
    try:
        args = parser.parse_args(argv)
        if args.command not in ("doctor", "verify-store"):
            raise fail(ErrorCode.CAPABILITY_UNAVAILABLE)
        if args.database is None:
            raise fail(ErrorCode.MALFORMED_REQUEST)
        inspect_sqlite(args.database)
        print(json.dumps({"status": "inspected", "trusted_backup": False}, sort_keys=True))
        return 0
    except ProblemError as error:
        print(json.dumps({"code": error.code.value, "retryable": error.retryable}, sort_keys=True))
        return 2
