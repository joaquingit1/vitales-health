from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from crew_health_device.adapters.file_journal import FileJournal
from crew_health_device.application.delivery import Undelivered, deliver, release, undelivered
from crew_health_device.application.ports import (
    Accepted,
    AcceptedReceipt,
    HubReply,
    JournalRecord,
    Refused,
    TransportFailure,
)

from .helpers import settings


@dataclass
class Hub:
    reply: HubReply
    calls: list[tuple[str, str, bytes]]

    def post_attempt(self, request_id: str, idempotency_key: str, body: bytes) -> HubReply:
        self.calls.append((request_id, idempotency_key, body))
        return self.reply


def journal(path: Path) -> FileJournal:
    return FileJournal(path, max_entries=20, max_bytes=20000)


def pending() -> JournalRecord:
    return JournalRecord("e07:acq-1:1", "send", "pending", "req-1", b'{"value":97.5}', None)


@pytest.mark.trace("T-E07-OX-03", "V16", "V35", "V25")
@pytest.mark.parametrize("receipt", [False, True])
def test_explicit_acceptance_or_exact_receipt_removes_pending_record(tmp_path: Path, receipt: bool) -> None:
    store = journal(tmp_path)
    store.put(pending())
    reply = (
        AcceptedReceipt("e07:acq-1:1", "recordAttempt", "req-1", "receipt-1", "2026-11-14T00:00:00Z")
        if receipt
        else Accepted(200)
    )
    report = deliver(store, Hub(reply, []), settings())
    assert report.receipts == int(receipt)
    assert report.sent == int(not receipt)
    assert not store.records()


@pytest.mark.trace("T-E07-OX-03", "V16", "V35", "V25")
@pytest.mark.parametrize(
    "key,operation,target",
    [
        ("other", "recordAttempt", "req-1"),
        ("e07:acq-1:1", "wrong", "req-1"),
        ("e07:acq-1:1", "recordAttempt", "other"),
    ],
)
def test_mismatched_receipt_is_held_and_never_resent(
    tmp_path: Path, key: str, operation: str, target: str
) -> None:
    store = journal(tmp_path)
    store.put(pending())
    hub = Hub(AcceptedReceipt(key, operation, target, "receipt-1", "2026-11-14T00:00:00Z"), [])
    assert deliver(store, hub, settings()).held == 1
    held = store.get(pending().key)
    assert held is not None and held.state == "held" and held.detail == "receipt_mismatch"
    assert deliver(store, hub, settings()).held == 0
    assert len(hub.calls) == 1


@pytest.mark.trace("T-E07-OX-03", "V16", "V35", "V25")
@pytest.mark.parametrize("code", ["ADAPTER_NOT_TRUSTED", "IDENTITY_MISMATCH", "INVALID_UNIT"])
def test_nonretryable_refusal_is_preserved_and_never_resent(tmp_path: Path, code: str) -> None:
    store = journal(tmp_path)
    store.put(pending())
    hub = Hub(Refused(code, False), [])
    assert deliver(store, hub, settings()).held == 1
    held = store.get(pending().key)
    assert held is not None and held.state == "held" and held.detail == code
    deliver(store, hub, settings())
    assert len(hub.calls) == 1


@pytest.mark.trace("T-E07-OX-03", "V16", "V35", "V25")
@pytest.mark.parametrize("transport", [False, True])
def test_retry_and_restart_preserve_byte_identical_body(tmp_path: Path, transport: bool) -> None:
    store = journal(tmp_path)
    record = pending()
    store.put(record)
    store.put(JournalRecord("acquisition:1", "acquisition", "started", "req-1", b"{}", None))
    hub = Hub(TransportFailure("timeout") if transport else Refused("TEMPORARY", True), [])
    first = deliver(store, hub, settings())
    assert first.failed_transport == int(transport)
    assert first.retry == int(not transport)
    restarted = journal(tmp_path)
    kept = restarted.get(record.key)
    assert kept is not None and (kept.state, kept.request_id, kept.body) == (
        "pending",
        record.request_id,
        record.body,
    )
    hub.reply = Accepted(200)
    assert deliver(restarted, hub, settings()).sent == 1
    assert hub.calls == [(record.request_id, record.key, record.body)] * 2
    assert len(restarted.records()) == 1


@pytest.mark.trace("T-E07-OX-03", "V16", "V35")
@pytest.mark.parametrize("marker_state,kept", [("completed", False), ("stopped", False), ("started", True)])
def test_acknowledgement_prunes_the_finished_acquisition_marker_but_never_an_unknown_one(
    tmp_path: Path, marker_state: str, kept: bool
) -> None:
    store = journal(tmp_path)
    store.put(JournalRecord("acquisition:acq-1", "acquisition", marker_state, "req-1", b"{}", None))
    store.put(pending())
    deliver(store, Hub(Accepted(200), []), settings())
    assert [r.key for r in store.records()] == (["acquisition:acq-1"] if kept else [])


@dataclass
class FlakyHub:
    replies: list[HubReply]
    calls: int = 0

    def post_attempt(self, request_id: str, idempotency_key: str, body: bytes) -> HubReply:
        self.calls += 1
        return self.replies.pop(0)


@pytest.mark.trace("T-E07-OX-03", "V16", "V35")
def test_retry_is_bounded_spaced_and_stops_once_the_hub_accepts(tmp_path: Path) -> None:
    from crew_health_device.application.delivery import deliver_with_retry

    store = journal(tmp_path)
    store.put(pending())
    waits: list[float] = []

    def wait(seconds: float) -> bool:
        waits.append(seconds)
        return False

    hub = FlakyHub([TransportFailure("timeout"), Refused("INTERNAL_ERROR", True), Accepted(200)])
    report = deliver_with_retry(store, hub, settings(), wait)
    assert (hub.calls, report.sent, store.records()) == (3, 1, ())
    assert waits == [settings().retry_backoff_ms / 1000] * 2


@pytest.mark.trace("T-E07-OX-03", "V16", "V35")
def test_retry_gives_up_after_retry_max_and_keeps_the_record_pending(tmp_path: Path) -> None:
    from crew_health_device.application.delivery import deliver_with_retry

    store = journal(tmp_path)
    store.put(pending())
    hub = FlakyHub([TransportFailure("timeout")] * settings().retry_max)
    deliver_with_retry(store, hub, settings(), lambda s: False)
    assert hub.calls == settings().retry_max
    assert [r.state for r in store.records()] == ["pending"]
    stopping = FlakyHub([TransportFailure("timeout")])
    deliver_with_retry(store, stopping, settings(), lambda s: True)
    assert stopping.calls == 1


@pytest.mark.trace("T-E07-OX-03", "V16", "V35")
def test_a_retryable_refusal_is_held_after_retry_max_sends_across_passes_and_restarts(tmp_path: Path) -> None:
    """Review minor 9: the count lives in the journal, so captures and restarts never reset it."""
    bounded = replace(settings(), retry_max=3)
    store = journal(tmp_path)
    store.put(pending())
    hub = Hub(Refused("INTERNAL_ERROR", True), [])
    assert deliver(store, hub, bounded).retry == 1
    assert deliver(journal(tmp_path), hub, bounded).retry == 1
    assert [r.detail for r in store.records()] == ["retryable:2:INTERNAL_ERROR"]
    report = deliver(journal(tmp_path), hub, bounded)
    assert (report.retry, report.held) == (0, 1)
    assert [(r.state, r.detail) for r in store.records()] == [("held", "retry_exhausted:INTERNAL_ERROR")]
    assert deliver(store, hub, bounded).held == 0 and len(hub.calls) == 3


@pytest.mark.trace("T-E07-OX-03", "V16", "V35")
def test_an_operator_release_sends_the_same_held_attempt_again_and_nothing_is_deleted(tmp_path: Path) -> None:
    store = journal(tmp_path)
    record = pending()
    store.put(
        JournalRecord(record.key, "send", "held", record.request_id, record.body, "retry_exhausted:BUSY")
    )
    store.put(JournalRecord("acquisition:acq-1", "acquisition", "completed", "req-1", b"{}", None))
    assert undelivered(store, 20) == (Undelivered(record.key, "req-1", "held", "retry_exhausted:BUSY"),)
    assert not release(store, "acquisition:acq-1") and not release(store, "e07:unknown:1")
    assert release(store, record.key)
    assert not release(store, record.key)
    hub = Hub(Accepted(200), [])
    assert deliver(store, hub, settings()).sent == 1
    assert hub.calls == [(record.request_id, record.key, record.body)]
    assert undelivered(store, 20) == ()
