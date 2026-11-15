"""Operator console of the warnings engine (E24 5.5): `crew-health rules pending`.

Lists the rows of `rules_pending`, the `evaluation_pending` marker of SYSTEM 8.6, one JSON line each. With
`--quarantine <pending_id>` an operator marks a stuck row reviewed so that clears resume; the row stays and health
stays degraded until it re-evaluates. The command is audited and never deletes a row. Exit status: 0 done, 1 refused
(unknown or not stuck row), 2 unusable input.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NoReturn, TextIO

from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.uow import Actor, UnitOfWorkFactory
from crew_health.rules.application.stores import PendingStore

_LIST_LIMIT = 10000  # the bound of rules.pending_max_rows (5.4); one extra overflow row may exist


@dataclass(frozen=True, slots=True)
class Console:
    stdout: TextIO
    stderr: TextIO
    operator_id: str
    correlation_id: str


class UsageError(Exception):
    """Arguments the command cannot use; nothing was read or changed."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _parser() -> _Parser:
    parser = _Parser(prog="crew-health rules", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    pending = commands.add_parser("pending", allow_abbrev=False)
    pending.add_argument("--quarantine", metavar="PENDING_ID")
    return parser


def _line(row: tuple[str, str, str, int, str, str, str | None]) -> dict[str, object]:
    pending_id, kind, state, attempts, code, first, nxt = row
    return {
        "pending_id": pending_id, "input_kind": kind, "state": state, "attempts": attempts,
        "last_code": code, "first_failed_at": first, "next_attempt_at": nxt,
    }  # fmt: skip


def run(argv: Sequence[str], uows: UnitOfWorkFactory, ids: IdFactory, console: Console) -> int:
    try:
        args = _parser().parse_args(list(argv))
    except UsageError as error:
        console.stderr.write(json.dumps({"usage": str(error)}) + "\n")
        return 2
    store = PendingStore()
    with uows.begin(
        actor=Actor("operator", console.operator_id), correlation_id=console.correlation_id
    ) as uow:
        if args.quarantine is not None:
            if not store.quarantine(uow.connection, args.quarantine):
                console.stderr.write(json.dumps({"refused": "not_a_stuck_row"}) + "\n")
                return 1
            uow.audit(
                AuditEntry(
                    ids.new("aud"), "rules.pending_quarantined", 1, uow.actor,
                    {"pending": args.quarantine}, uow.correlation_id, None, {},
                )
            )  # fmt: skip
        for row in store.rows(uow.connection, limit=_LIST_LIMIT):
            console.stdout.write(json.dumps(_line(row), sort_keys=True) + "\n")
    return 0
