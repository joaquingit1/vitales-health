"""E13 HTTP boundary; the facade owns transactions, current grants and command replay."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from crew_health.kernel.commands import CommandIdentity
from crew_health.kernel.uow import SessionContext


@dataclass(frozen=True, slots=True)
class HttpResult:
    body: Mapping[str, object]
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QueryParameters:
    cursor: str | None
    limit: int | None


@dataclass(frozen=True, slots=True)
class CommandRequest:
    identity: CommandIdentity
    body: Mapping[str, object]
    config_release_id: str | None
    expected_revision: int | None


class AssistantHttp(Protocol):
    def query(
        self,
        session: SessionContext,
        operation: str,
        target: str | None,
        parameters: QueryParameters,
    ) -> HttpResult: ...

    def command(
        self,
        session: SessionContext,
        operation: str,
        target: str | None,
        request: CommandRequest,
    ) -> HttpResult: ...

    def stream(self, session: SessionContext, conversation_id: str) -> Iterator[bytes]: ...
