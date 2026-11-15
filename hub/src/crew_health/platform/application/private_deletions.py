"""Durable private deletion intents (SYSTEM 5.8.1): the rows a deletion reserves before any vault call and the pages
recovery resumes."""

from dataclasses import dataclass

from pydantic import ValidationError

from crew_health.kernel.commands import CommandIdentity
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.platform.application.uow import codec


@dataclass(frozen=True, slots=True)
class Deletion:
    identity: CommandIdentity
    owner_person_id: str
    entry_ids: tuple[str, ...]
    revocation_id: str | None
    completed: bool


def decode_deletion(row: tuple[object, ...]) -> Deletion:
    identity, owner, entries, revocation, completed = row
    if (
        not isinstance(identity, bytes)
        or not isinstance(owner, str)
        or not isinstance(entries, bytes)
        or not (revocation is None or isinstance(revocation, str))
        or completed not in (0, 1)
    ):
        raise StorageUnavailable("corrupt")
    try:
        return Deletion(
            codec(CommandIdentity).validate_json(identity),
            owner,
            codec(tuple[str, ...]).validate_json(entries),
            revocation,
            bool(completed),
        )
    except ValidationError as error:
        raise StorageUnavailable("corrupt") from error


def deletion_for(view: ReadSnapshot | UnitOfWork, identity: CommandIdentity) -> Deletion | None:
    rows = view.connection.rows(
        "SELECT identity,owner_person_id,entry_ids,revocation_id,completed FROM platform_private_deletion WHERE actor_ref=? AND command_key=?",
        (identity.actor_ref, identity.key),
        limit=1,
    )
    return decode_deletion(rows[0]) if rows else None


def pending_deletions(view: ReadSnapshot, *, after: tuple[str, str], limit: int) -> tuple[Deletion, ...]:
    rows = view.connection.rows(
        "SELECT identity,owner_person_id,entry_ids,revocation_id,completed FROM platform_private_deletion WHERE completed=0 AND (actor_ref,command_key)>(?,?) ORDER BY actor_ref,command_key LIMIT ?",
        (*after, limit),
        limit=limit,
    )
    return tuple(decode_deletion(row) for row in rows)
