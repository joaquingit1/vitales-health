"""One bounded delivery pass; refusals stay held for human resolution, never deleted."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from crew_health_device.application.ports import (
    Accepted,
    AcceptedReceipt,
    HubAttempts,
    Journal,
    JournalRecord,
    Refused,
)
from crew_health_device.application.settings import AdapterSettings


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    sent: int
    receipts: int
    held: int
    retry: int
    failed_transport: int


def _matches(receipt: AcceptedReceipt, record: JournalRecord) -> bool:
    # Only the accepted_command envelope produces this receipt type at the transport boundary.
    return (
        receipt.command_key == record.key
        and receipt.operation_id == "recordAttempt"
        and receipt.target == record.request_id
    )


def _acknowledged(journal: Journal, record: JournalRecord) -> None:
    """The hub holds the attempt now: drop the send record, then the finished acquisition marker, so the
    bounded journal never fills with history. A marker still "started" is kept (unknown physical state)."""
    journal.remove(record.key)
    acquisition_id = record.key.removeprefix("e07:").rsplit(":", 1)[0]
    marker = journal.get("acquisition:" + acquisition_id)
    if marker is not None and marker.state != "started":
        journal.remove(marker.key)


def _refusals(record: JournalRecord) -> int:
    """Retryable refusals already met by this pending send, kept in its detail as `retryable:<n>:<code>`."""
    if record.detail is None or not record.detail.startswith("retryable:"):
        return 0
    count = record.detail.split(":", 2)[1]
    return int(count) if count.isdigit() else 0


@dataclass(frozen=True, slots=True)
class Undelivered:
    key: str
    request_id: str
    state: str
    detail: str | None


def undelivered(journal: Journal, limit: int) -> tuple[Undelivered, ...]:
    """Sends the hub does not hold yet, pending or held, at most `limit` (the journal's own bound)."""
    return tuple(
        Undelivered(record.key, record.request_id, record.state, record.detail)
        for record in journal.records()[:limit]
        if record.kind == "send"
    )


def release(journal: Journal, key: str) -> bool:
    """Operator resolution of a held send: back to pending with a fresh refusal count, so the next pass sends the
    same idempotent attempt again. Nothing is deleted; a key that is not a held send is left alone."""
    record = journal.get(key)
    if record is None or record.kind != "send" or record.state != "held":
        return False
    journal.put(replace(record, state="pending", detail=None))
    return True


def deliver(journal: Journal, hub: HubAttempts, settings: AdapterSettings) -> DeliveryReport:
    sent = receipts = held = retry = failed_transport = 0
    for record in journal.records()[: settings.journal_max_entries]:
        if record.kind != "send" or record.state != "pending":
            continue
        reply = hub.post_attempt(record.request_id, record.key, record.body)
        if isinstance(reply, Accepted):
            _acknowledged(journal, record)
            sent += 1
        elif isinstance(reply, AcceptedReceipt):
            if _matches(reply, record):
                _acknowledged(journal, record)
                receipts += 1
            else:
                journal.put(replace(record, state="held", detail="receipt_mismatch"))
                held += 1
        elif isinstance(reply, Refused):
            if reply.retryable and _refusals(record) + 1 < settings.retry_max:
                journal.put(replace(record, detail=f"retryable:{_refusals(record) + 1}:{reply.code}"))
                retry += 1
            else:
                # A retryable refusal that outlasts retry_max sends, across passes, captures and restarts, is held
                # like any other refusal (review minor 9): bounded, and visible on /deliveries for an operator.
                detail = reply.code if not reply.retryable else "retry_exhausted:" + reply.code
                journal.put(replace(record, state="held", detail=detail))
                held += 1
        else:
            failed_transport += 1
    return DeliveryReport(sent, receipts, held, retry, failed_transport)


def deliver_with_retry(
    journal: Journal, hub: HubAttempts, settings: AdapterSettings, wait: Callable[[float], bool]
) -> DeliveryReport:
    """At most `retry_max` passes, `retry_backoff_ms` apart (an elapsed, relative timer), while sends are still
    pending for a retryable reason. `wait` returns True when the process is stopping. Held records never retry;
    whatever is still pending stays in the journal for the next capture or the startup pass."""
    report = deliver(journal, hub, settings)
    for _ in range(settings.retry_max - 1):
        if report.retry + report.failed_transport == 0 or wait(settings.retry_backoff_ms / 1000):
            break
        report = deliver(journal, hub, settings)
    return report
