"""Console commands of the identity module (E02 4): roster document, credentials, adapter tokens.

E20 discovers and runs them with the hub's own services (R-E02-22). Secrets come from standard input, one per
line, never from arguments, the environment or a file. Results are JSON lines that carry no secret, except the
adapter token, which is printed once. Exit status: 0 done, 1 refused by the hub, 2 unusable input.

Standard input, in order: the operator credential, only once the provisioned operator has one; then, for
`credential set`, the new credential; for `credential change`, the current and then the new credential (the
owner proves the change, no operator is involved).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import NoReturn, TextIO

from jsonschema import Draft202012Validator

from crew_health.identity.application.admin import IdentityAdmin
from crew_health.identity.domain.roster import (
    Namespace,
    ProvisionedPrincipal,
    RosterBootstrap,
    RosterMember,
)
from crew_health.identity.domain.session import PrincipalKind
from crew_health.kernel.errors import ErrorCode, FieldError, ProblemError

SCHEMA = "schemas/roster-bootstrap.schema.json"
_FIELD_ERRORS_MAX = 16


@dataclass(frozen=True, slots=True)
class Console:
    """The operator's terminal and the bounds the composer reads from hub settings."""

    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    correlation_id: str
    client: str
    max_document_bytes: int
    max_secret_bytes: int


class UsageError(Exception):
    """Arguments or input the command cannot use; nothing was sent to the hub."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def read_secret(console: Console) -> str:
    """One line of standard input without its line ending; bounded before it is decoded further."""
    line = console.stdin.readline(console.max_secret_bytes + 2)
    secret = line.removesuffix("\n").removesuffix("\r")
    if not secret or "\n" in secret or len(secret.encode("utf-8")) > console.max_secret_bytes:
        raise UsageError("expected one secret line on standard input")
    return secret


def _malformed(errors: Sequence[FieldError] = ()) -> ProblemError:
    return ProblemError(
        ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value, field_errors=tuple(errors)
    )


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(files("crew_health.identity").joinpath(SCHEMA).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError("non-finite number")


def parse_roster(raw: bytes) -> RosterBootstrap:
    """Strict JSON (no duplicate keys, no NaN), checked against the roster schema before any conversion."""
    try:
        document: object = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_reject_constant
        )
    except UnicodeDecodeError, ValueError, RecursionError:
        raise _malformed() from None
    errors = sorted(_validator().iter_errors(document), key=lambda error: list(error.absolute_path))
    if errors:
        raise _malformed(
            tuple(
                FieldError(
                    "/" + "/".join(str(part) for part in error.absolute_path),
                    ErrorCode.MALFORMED_REQUEST.value,
                    error.validator if isinstance(error.validator, str) else "schema",
                )
                for error in errors[:_FIELD_ERRORS_MAX]
            )
        )
    return _bootstrap(document)


def _bootstrap(document: object) -> RosterBootstrap:
    if not isinstance(document, dict):
        raise _malformed()
    members = tuple(
        RosterMember(
            str(item["person_id"]),
            str(item["display_name"]),
            int(item["roster_index"]),
            int(item["on_roster_from_day"]),
            None if item["on_roster_until_day"] is None else int(item["on_roster_until_day"]),
            None if item["medical_officer_rank"] is None else int(item["medical_officer_rank"]),
        )
        for item in document["members"]
    )
    principals = tuple(
        ProvisionedPrincipal(str(item["principal_id"]), _kind(item["kind"]), str(item["display_name"]))
        for item in document["principals"]
    )
    return RosterBootstrap(
        str(document["mission_id"]), _namespace(document["namespace"]), members, principals
    )


_KINDS: dict[str, PrincipalKind] = {
    "station": "station",
    "ground": "ground",
    "operator": "operator",
    "editor": "editor",
    "adapter": "adapter",
}
_NAMESPACES: dict[str, Namespace] = {"LIVE": "LIVE", "REPLAY": "REPLAY", "SIM": "SIM"}


def _kind(value: object) -> PrincipalKind:
    if not isinstance(value, str) or value not in _KINDS:
        raise _malformed()
    return _KINDS[value]


def _namespace(value: object) -> Namespace:
    if not isinstance(value, str) or value not in _NAMESPACES:
        raise _malformed()
    return _NAMESPACES[value]


def read_document(path: Path, *, max_bytes: int) -> bytes:
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError:
        raise UsageError("the roster document cannot be read") from None
    if len(raw) > max_bytes:
        raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, ErrorCode.PAYLOAD_TOO_LARGE.value)
    return raw


def _parser() -> _Parser:
    parser = _Parser(prog="crew-health identity", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    roster = commands.add_parser("roster", allow_abbrev=False).add_subparsers(dest="action", required=True)
    apply = roster.add_parser("apply", allow_abbrev=False)
    apply.add_argument("document", type=Path)
    apply.add_argument("--expected-revision", type=int, required=True)
    credential = commands.add_parser("credential", allow_abbrev=False).add_subparsers(
        dest="action", required=True
    )
    for name in ("set", "change"):
        credential.add_parser(name, allow_abbrev=False).add_argument("principal_id")
    token = commands.add_parser("adapter-token", allow_abbrev=False)
    token.add_argument("principal_id")
    token.add_argument("--listener", required=True)
    return parser


def _emit(console: Console, value: dict[str, object]) -> None:
    console.stdout.write(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")


def _run(args: argparse.Namespace, admin: IdentityAdmin, console: Console) -> None:
    context = {"client": console.client, "correlation_id": console.correlation_id}
    if args.command == "roster":
        document = parse_roster(read_document(args.document, max_bytes=console.max_document_bytes))
        operators = [p.principal_id for p in document.principals if p.kind == "operator"]
        operator = admin.console_operator(
            lambda: read_secret(console), bootstrap=operators[0] if len(operators) == 1 else None, **context
        )
        result = admin.apply_roster(
            document,
            expected_revision=args.expected_revision,
            operator=operator,
            correlation_id=console.correlation_id,
        )
        _emit(console, {"revision": result.revision, "removed": list(result.removed)})
    elif args.command == "credential" and args.action == "set":
        operator = admin.console_operator(lambda: read_secret(console), **context)
        generation = admin.set_credential(
            args.principal_id, read_secret(console), current_credential=None, operator=operator, **context
        )
        _emit(console, {"principal_id": args.principal_id, "generation": generation})
    elif args.command == "credential":
        current, new = read_secret(console), read_secret(console)
        generation = admin.set_credential(
            args.principal_id, new, current_credential=current, operator=None, **context
        )
        _emit(console, {"principal_id": args.principal_id, "generation": generation})
    else:
        operator = admin.console_operator(lambda: read_secret(console), **context)
        view = admin.issue_adapter_token(
            args.principal_id,
            listener=args.listener,
            operator=operator,
            correlation_id=console.correlation_id,
        )
        _emit(
            console,
            {
                "session_id": view.record.session_id,
                "expires_at": view.record.expires_at.isoformat(),
                "token": view.token,
            },
        )


def main(argv: Sequence[str], admin: IdentityAdmin, console: Console) -> int:
    try:
        _run(_parser().parse_args(list(argv)), admin, console)
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
