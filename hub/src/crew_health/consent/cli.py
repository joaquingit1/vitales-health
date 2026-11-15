"""Owner console commands of the consent module (E02 3.3): grant, revoke and list one's own grants.

Consent has no HTTP operation yet (R-E02-1); this is the authenticated owner path. The owner proves the
credential on standard input (one line, never an argument) under the sign-in limits, and only the subject's
own grants are ever listed or changed. Results are JSON lines naming who receives what, for which object and
purpose, until when. A list shows every active grant and the newest ended ones, then one
`{"older_ended_grants": true}` line when earlier ended grants were left out. Exit status: 0 done, 1 refused
by the hub, 2 unusable input.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn, TextIO

from crew_health.consent.application.service import ConsentService
from crew_health.consent.domain.grants import Grant, GrantRequest, OriginMode
from crew_health.identity.application.ports import OwnerVerification
from crew_health.kernel.clock import Clock
from crew_health.kernel.errors import ProblemError

_ORIGINS: dict[str, OriginMode] = {"LIVE": "LIVE", "REPLAY": "REPLAY", "SIM": "SIM"}


@dataclass(frozen=True, slots=True)
class Console:
    """The owner's terminal and the secret bound the composer reads from hub settings."""

    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    correlation_id: str
    client: str
    max_secret_bytes: int


class UsageError(Exception):
    """Arguments or input the command cannot use; nothing was sent to the hub."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _secret(console: Console) -> str:
    line = console.stdin.readline(console.max_secret_bytes + 2)
    secret = line.removesuffix("\n").removesuffix("\r")
    if not secret or "\n" in secret or len(secret.encode("utf-8")) > console.max_secret_bytes:
        raise UsageError("expected the owner credential as one line on standard input")
    return secret


def _instant(value: str) -> datetime:
    """An RFC 3339 UTC instant ending in Z, as the contract's Instant."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise UsageError("--until must be an RFC 3339 UTC instant ending in Z") from None
    if not value.endswith("Z") or parsed.utcoffset() is None or parsed.utcoffset() != UTC.utcoffset(None):
        raise UsageError("--until must be an RFC 3339 UTC instant ending in Z")
    return parsed.astimezone(UTC)


def _origin(value: str) -> OriginMode:
    if value not in _ORIGINS:
        raise UsageError("--origin must be LIVE, REPLAY or SIM")
    return _ORIGINS[value]


def _parser() -> _Parser:
    parser = _Parser(prog="crew-health consent", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    grant = commands.add_parser("grant", allow_abbrev=False)
    for name in ("--person", "--recipient", "--mission", "--origin", "--object", "--purpose"):
        grant.add_argument(name, required=True)
    grant.add_argument("--action", action="append", required=True)
    grant.add_argument("--field", action="append", required=True)
    grant.add_argument("--until")
    revoke = commands.add_parser("revoke", allow_abbrev=False)
    revoke.add_argument("--person", required=True)
    revoke.add_argument("--grant", required=True)
    revoke.add_argument("--expected-revision", type=int, required=True)
    commands.add_parser("list", allow_abbrev=False).add_argument("--person", required=True)
    return parser


def grant_line(grant: Grant) -> dict[str, object]:
    request = grant.request
    return {
        "grant_id": grant.grant_id,
        "revision": grant.revision,
        "state": grant.state,
        "recipient": request.recipient,
        "object_ref": request.object_ref,
        "purpose": request.purpose,
        "actions": sorted(request.actions),
        "fields": sorted(request.fields),
        "mission_id": request.mission_id,
        "origin_mode": request.origin_mode,
        "valid_from": request.valid_from.isoformat(),
        "valid_until": None if request.valid_until is None else request.valid_until.isoformat(),
    }


def _emit(console: Console, value: dict[str, object]) -> None:
    console.stdout.write(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")


def _run(
    args: argparse.Namespace,
    identity: OwnerVerification,
    consent: ConsentService,
    clock: Clock,
    console: Console,
) -> None:
    until = None if args.command != "grant" or args.until is None else _instant(args.until)
    origin = _origin(args.origin) if args.command == "grant" else None
    owner = identity.verify_owner(
        args.person, _secret(console), client=console.client, correlation_id=console.correlation_id
    )
    if args.command == "grant" and origin is not None:
        request = GrantRequest(
            args.recipient,
            args.mission,
            origin,
            args.object,
            args.purpose,
            frozenset(args.action),
            frozenset(args.field),
            clock.now(),
            until,
        )
        _emit(console, grant_line(consent.grant(owner, request, correlation_id=console.correlation_id)))
    elif args.command == "revoke":
        revoked = consent.revoke(
            owner,
            args.grant,
            expected_revision=args.expected_revision,
            correlation_id=console.correlation_id,
        )
        _emit(console, grant_line(revoked))
    else:
        listed = consent.own_grants(owner)
        for grant in listed.grants:
            _emit(console, grant_line(grant))
        if listed.older:
            _emit(console, {"older_ended_grants": True})


def main(
    argv: Sequence[str],
    identity: OwnerVerification,
    consent: ConsentService,
    clock: Clock,
    console: Console,
) -> int:
    try:
        _run(_parser().parse_args(list(argv)), identity, consent, clock, console)
    except UsageError as error:
        console.stderr.write(json.dumps({"usage": str(error)}) + "\n")
        return 2
    except ProblemError as error:
        failure = {
            "code": error.code.value,
            "retryable": error.retryable,
            "field_errors": [item.field for item in error.field_errors],
        }
        console.stderr.write(json.dumps(failure, sort_keys=True) + "\n")
        return 1
    return 0
