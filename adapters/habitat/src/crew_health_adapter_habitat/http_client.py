"""The HTTP client of the hub boundary: operationId ingestReadings, POST /v1/adapters/{adapter_id}/readings.

Every origin (LIVE, REPLAY, SIM) goes through this one path. Every call has a timeout. The body and the
Idempotency-Key of a queued batch are frozen: a retry, a restart and a long outage resend the same bytes. The
generated IngestResult and ErrorEnvelope models decide what the hub said. A batch is acknowledged only by a result
that reconciles with it index by index, or by the closed accepted_command receipt of S5.8 that names this exact
key, operation and target. A mismatched 409 never dequeues the batch and never mints a new key.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.contracts_gen.schemas.ingest_result_schema import IngestResult
from crew_health.contracts_gen.schemas.requests_schema import Reading
from pydantic import ValidationError

from crew_health_adapter_habitat.errors import CommandConflict, ResultMismatch, RetryExhausted, TransportFault
from crew_health_adapter_habitat.ports import Settings, Sleeper
from crew_health_adapter_habitat.queue import DurableQueue, FrozenBatch, RejectedRow

OPERATION_ID = "ingestReadings"
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class SendResult:
    kind: Literal["result", "receipt", "retry", "refused"]
    code: str = ""
    result: IngestResult | None = None


def validate_reading(row: Mapping[str, Any]) -> bool:
    """The generated per-row boundary of AdapterReadingBatch: a row the contract refuses is never queued."""
    try:
        Reading.model_validate(row)
    except ValidationError:
        return False
    return True


def target_of(adapter_id: str) -> str:
    return "/v1/adapters/" + adapter_id + "/readings"


class HubClient:
    """One attempt per call. `client` is any httpx.Client already bound to the hub's base URL."""

    def __init__(self, client: httpx.Client, adapter_id: str, token: str, settings: Settings) -> None:
        self.client, self.adapter_id, self.token, self.settings = client, adapter_id, token, settings

    def send(self, batch: FrozenBatch) -> SendResult:
        try:
            response = self.client.post(
                target_of(self.adapter_id),
                content=batch.body,
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Idempotency-Key": batch.key,
                    "Content-Type": "application/json",
                },
                timeout=float(self.settings.http_timeout_s),
            )
        except httpx.TimeoutException:
            return SendResult("retry", "TIMEOUT")
        except httpx.TransportError:
            return SendResult("retry", "HUB_UNREACHABLE")
        return self._read(response, batch)

    def _read(self, response: httpx.Response, batch: FrozenBatch) -> SendResult:
        if response.status_code == 200:
            try:
                return SendResult("result", result=IngestResult.model_validate(response.json()))
            except ValueError, ValidationError:
                return SendResult("refused", "RESULT_INVALID")
        envelope = _envelope(response)
        if response.status_code == 409:
            return self._conflict(envelope, batch)
        if response.status_code in RETRYABLE_STATUS:
            return SendResult("retry", "HTTP_" + str(response.status_code))
        return SendResult(
            "refused", envelope.code if envelope is not None else "HTTP_" + str(response.status_code)
        )

    def _conflict(self, envelope: ErrorEnvelope | None, batch: FrozenBatch) -> SendResult:
        state = None if envelope is None else envelope.current_state
        if envelope is None or envelope.code != "DUPLICATE_COMMAND" or state is None:
            return SendResult("refused", "CONFLICT")
        found = state.model_dump() if hasattr(state, "model_dump") else dict(state)
        receipt = found.get("root", found) if isinstance(found, dict) else {}
        matches = (
            isinstance(receipt, dict)
            and receipt.get("kind") == "accepted_command"
            and receipt.get("command_key") == batch.key
            and receipt.get("operation_id") == OPERATION_ID
            and receipt.get("target") == target_of(self.adapter_id)
            and isinstance(receipt.get("receipt_id"), str)
            and isinstance(receipt.get("accepted_at"), str)
        )
        return SendResult("receipt") if matches else SendResult("refused", "CONFLICT")


def _envelope(response: httpx.Response) -> ErrorEnvelope | None:
    try:
        return ErrorEnvelope.model_validate(response.json())
    except ValueError, ValidationError:
        return None


def reconcile(batch: FrozenBatch, result: IngestResult) -> tuple[RejectedRow, ...]:
    """Check the per-index result against the batch. Returns the rejected rows; raises ResultMismatch otherwise."""
    rejected = result.rejected
    indexes = [item.index for item in rejected]
    if (
        result.batch_id.root != batch.batch_id
        or len(set(indexes)) != len(indexes)
        or any(i >= batch.row_count for i in indexes)
        or result.accepted + result.duplicates + len(rejected) != batch.row_count
        or len(result.reading_ids) != result.accepted
    ):
        raise ResultMismatch("result does not reconcile with the batch")
    return tuple(RejectedRow(item.index, item.code) for item in rejected)


def backoff_s(settings: Settings, attempt: int) -> float:
    """Capped exponential wait before attempt+1: batch_interval_s doubling, at most retry_max_backoff_s."""
    return float(min(settings.retry_max_backoff_s, settings.batch_interval_s * 2 ** max(attempt - 1, 0)))


class Sender:
    """Drains the queue through the hub. Retries are bounded; exhaustion keeps every row and raises."""

    def __init__(
        self,
        hub: HubClient,
        queue: DurableQueue,
        sleeper: Sleeper,
        on_event: Callable[[str], None] = lambda code: None,
    ) -> None:
        self.hub, self.queue, self.sleeper, self.on_event = hub, queue, sleeper, on_event
        self.settings = hub.settings

    def flush(self, max_batches: int) -> int:
        """Send up to `max_batches` batches in order. Returns how many were acknowledged."""
        done = 0
        while done < max_batches:
            batch = self.queue.next_batch()
            if batch is None:
                break
            if batch.state != "open":
                raise CommandConflict(batch.state)
            self._deliver(batch)
            done += 1
        return done

    def _deliver(self, batch: FrozenBatch) -> None:
        for attempt in range(1, self.settings.retry_max + 1):
            self.queue.note_attempt(batch.batch_id)
            sent = self.hub.send(batch)
            if sent.kind == "result" and sent.result is not None:
                self._finish(batch, sent.result)
                return
            if sent.kind == "receipt":
                self.queue.acknowledge(batch, (), receipt_only=True)
                self.on_event("RECEIPT_ONLY")
                return
            if sent.kind == "refused":
                self.on_event(sent.code)
                if sent.code == "CONFLICT":
                    self.queue.quarantine(batch.batch_id, sent.code)
                    raise CommandConflict(sent.code)
                raise TransportFault(sent.code)
            self.on_event(sent.code)
            if attempt < self.settings.retry_max:
                self.sleeper.sleep(backoff_s(self.settings, attempt))
        raise RetryExhausted(batch.batch_id)

    def _finish(self, batch: FrozenBatch, result: IngestResult) -> None:
        try:
            rejected = reconcile(batch, result)
        except ResultMismatch:
            self.queue.quarantine(batch.batch_id, "RESULT_MISMATCH")
            self.on_event("RESULT_MISMATCH")
            raise
        self.queue.acknowledge(batch, rejected)
        if rejected:
            self.on_event("ROWS_REJECTED")


def body_rows(batch: FrozenBatch) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = json.loads(batch.body)["readings"]
    return rows
