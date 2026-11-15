"""Ports implemented by hardware, clocks, storage and the hub transport."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health_device.domain.acquisition import OriginMode


class TimeSource(Protocol):
    def now(self) -> datetime: ...

    def monotonic_ns(self) -> int: ...


class SourceUnavailable(Exception):
    def __init__(self, reason: Literal["radio_absent", "device_absent", "connect_timeout"]) -> None:
        self.reason = reason
        super().__init__(reason)


class SourceDisconnected(Exception):
    """The source lost the device or overflowed its bounded queue."""

    def __init__(self, reason: Literal["disconnected", "queue_overflow"] = "disconnected") -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class RawFrame:
    characteristic: str
    payload: bytes


class FrameSource(Protocol):
    @property
    def origin(self) -> OriginMode: ...

    def open(self, timeout_ms: int) -> None: ...

    def next_frame(self, timeout_ms: int) -> RawFrame | None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Accepted:
    status: int


@dataclass(frozen=True, slots=True)
class AcceptedReceipt:
    command_key: str
    operation_id: str
    target: str
    receipt_id: str
    accepted_at: str


@dataclass(frozen=True, slots=True)
class Refused:
    code: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class TransportFailure:
    reason: str


type HubReply = Accepted | AcceptedReceipt | Refused | TransportFailure


class HubAttempts(Protocol):
    def post_attempt(self, request_id: str, idempotency_key: str, body: bytes) -> HubReply: ...


@dataclass(frozen=True, slots=True)
class JournalRecord:
    key: str
    kind: Literal["acquisition", "send"]
    state: str
    request_id: str
    body: bytes
    detail: str | None


class JournalFull(Exception):
    """A configured journal capacity would be exceeded."""


class JournalUnavailable(Exception):
    def __init__(self, cause: str) -> None:
        self.cause = cause
        super().__init__(cause)


class JournalCorrupt(Exception):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class Journal(Protocol):
    def put(self, record: JournalRecord) -> None: ...

    def get(self, key: str) -> JournalRecord | None: ...

    def records(self) -> tuple[JournalRecord, ...]: ...

    def remove(self, key: str) -> None: ...
